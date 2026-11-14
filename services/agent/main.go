package main

// Stable Agent: owns an embedded rootless Docker daemon, installs a replaceable
// Node container, and exposes a fixed local Unix-socket management API.
// The Node (not Agent) owns the existing binary WSS and child VPN Cores.
import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"syscall"
	"time"
)

const (
	nodeContainer = "waylane-node-runtime"
	nodeSeed      = "waylane-node-seed:bootstrap"
	agentSocket   = "/var/lib/waylane-agent/control/agent.sock"
	dockerSocket  = "/run/user/1000/docker.sock"
)

var nodeDigest = regexp.MustCompile(`^ghcr\.io/kobaproduction/waylane-node@sha256:[0-9a-f]{64}$`)

type dockerRun func(context.Context, ...string) (string, error)

func systemDocker(ctx context.Context, args ...string) (string, error) {
	// The installed Agent root filesystem is read-only. Docker CLI and
	// buildx create config/context files under ~/.docker by default;
	// confine them to the existing private writable /tmp tmpfs instead.
	configDir := "/tmp/waylane-docker-cli"
	if err := os.MkdirAll(configDir, 0700); err != nil {
		return "", errors.New("cannot prepare private Docker CLI config")
	}
	argv := append([]string{"--host", "unix://" + dockerSocket}, args...)
	cmd := exec.CommandContext(ctx, "docker", argv...)
	cmd.Env = append(os.Environ(), "DOCKER_CONFIG="+configDir)
	output, err := cmd.Output()
	if err != nil {
		return "", errors.New("private rootless Docker request failed")
	}
	if len(output) > 8192 {
		return "", errors.New("Docker output size exceeded")
	}
	return strings.TrimSpace(string(output)), nil
}
func checkDocker(ctx context.Context, run dockerRun) error {
	res, err := run(ctx, "info", "--format", "{{json .SecurityOptions}}")
	if err != nil || !strings.Contains(res, "rootless") {
		return errors.New("expected embedded rootless Docker")
	}
	return nil
}
func startDockerd(ctx context.Context) (<-chan error, error) {
	if os.Geteuid() == 0 {
		return nil, errors.New("Agent must not run as root")
	}
	if err := reclaimStaleContainerdRuntime(filepath.Join(filepath.Dir(dockerSocket), "docker/containerd")); err != nil {
		return nil, fmt.Errorf("unsafe private Docker runtime state: %w", err)
	}
	cmd := exec.CommandContext(ctx, "dockerd-entrypoint.sh", "dockerd", "--host=unix://"+dockerSocket, "--storage-driver=vfs", "--tls=false")
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Cancel = func() error {
		if cmd.Process == nil {
			return nil
		}
		return syscall.Kill(-cmd.Process.Pid, syscall.SIGTERM)
	}
	cmd.WaitDelay = 10 * time.Second
	if err := cmd.Start(); err != nil {
		return nil, errors.New("unable to start embedded rootless Docker")
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	return done, nil
}
func awaitDockerd(ctx context.Context, run dockerRun, done <-chan error) error {
	timeout := time.NewTimer(65 * time.Second)
	defer timeout.Stop()
	tick := time.NewTicker(time.Second)
	defer tick.Stop()
	for {
		probe, cancel := context.WithTimeout(ctx, 3*time.Second)
		err := checkDocker(probe, run)
		cancel()
		if err == nil {
			return nil
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-done:
			return errors.New("embedded rootless Docker exited")
		case <-timeout.C:
			return errors.New("embedded Docker startup timeout")
		case <-tick.C:
		}
	}
}

type manager struct {
	mu                                           sync.Mutex
	run                                          dockerRun
	stateDir                                     string
	panelURL, discoveryToken, nodeToken, devHTTP string
	image                                        string
	operation                                    *nodeReleaseOperation
	ready                                        chan struct{}
	readyDone                                    bool
}

func (m *manager) releasePath() string { return filepath.Join(m.stateDir, "node-release.json") }
func (m *manager) loadRelease() error {
	info, statErr := os.Lstat(m.releasePath())
	if statErr == nil && (!info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || info.Size() > 4096) {
		return errors.New("Node release must be a private regular file")
	}
	if statErr != nil && !errors.Is(statErr, os.ErrNotExist) {
		return errors.New("cannot stat Node release")
	}
	raw, err := os.ReadFile(m.releasePath())
	if errors.Is(err, os.ErrNotExist) {
		m.image = nodeSeed
		return nil
	}
	if err != nil {
		return errors.New("cannot read Node release")
	}
	var doc struct {
		Version int    `json:"version"`
		Image   string `json:"image"`
	}
	if err = json.Unmarshal(raw, &doc); err != nil || doc.Version != 1 || !nodeDigest.MatchString(doc.Image) {
		return errors.New("invalid persisted Node release")
	}
	m.image = doc.Image
	return nil
}
func (m *manager) saveRelease(image string) error {
	if !nodeDigest.MatchString(image) {
		return errors.New("Node image must be pinned to approved GHCR digest")
	}
	raw, _ := json.Marshal(struct {
		Version int    `json:"version"`
		Image   string `json:"image"`
	}{1, image})
	temp, err := os.CreateTemp(m.stateDir, ".node-release-")
	if err != nil {
		return err
	}
	defer os.Remove(temp.Name())
	if err = temp.Chmod(0600); err == nil {
		_, err = temp.Write(raw)
	}
	if err == nil {
		err = temp.Sync()
	}
	if e := temp.Close(); err == nil {
		err = e
	}
	if err != nil {
		return errors.New("cannot save Node release")
	}
	return os.Rename(temp.Name(), m.releasePath())
}
func (m *manager) ownedNode(ctx context.Context) (bool, error) {
	label, err := m.run(ctx, "container", "inspect", "--format", "{{index .Config.Labels \"waylane.component\"}}", nodeContainer)
	if err != nil {
		if checkDocker(ctx, m.run) != nil {
			return false, errors.New("rootless Docker unavailable")
		}
		return false, nil
	}
	if label != "node" {
		return false, errors.New("a non-WayLane container has the Node name")
	}
	return true, nil
}
func (m *manager) stageImage(ctx context.Context, image string) error {
	if image == nodeSeed {
		// The Agent image bundles the seed binary. A previously cached image
		// with this stable tag may contain an older binary: Docker rebuild
		// detects updated COPY input and re-tags it without deleting data.
		if _, err := m.run(ctx, "build", "--tag", nodeSeed, "-f", "/opt/waylane/node-seed/Dockerfile", "/opt/waylane/node-seed"); err != nil {
			return errors.New("Node seed build failed")
		}
		return nil
	}
	if !nodeDigest.MatchString(image) {
		return errors.New("unapproved or mutable Node image reference")
	}
	if _, err := m.run(ctx, "pull", image); err != nil {
		return errors.New("Node image pull failed")
	}
	return nil
}
func (m *manager) startNode(ctx context.Context, image string) error {
	if err := m.stageImage(ctx, image); err != nil {
		return err
	}
	return m.startNodeStaged(ctx, image)
}

// The target must already exist in the private daemon. Used for verified
// cutover and offline rollback: never re-pull old images during an outage.
func (m *manager) startNodeStaged(ctx context.Context, image string) error {
	if image != nodeSeed && !nodeDigest.MatchString(image) {
		return errors.New("unapproved staged Node image")
	}
	// Private ephemeral file prevents secrets appearing in process arguments.
	envFile, err := os.CreateTemp(m.stateDir, ".node-env-")
	if err != nil {
		return err
	}
	defer os.Remove(envFile.Name())
	if err = envFile.Chmod(0600); err != nil {
		envFile.Close()
		return err
	}
	for _, p := range [][2]string{
		{"NODE_MANAGED_BY_AGENT", "true"}, {"NODE_STATE_DIR", m.stateDir}, {"ROOTLESS_SOCKET", dockerSocket},
		{"CONTROL_PANEL_URL", m.panelURL}, {"DISCOVERY_TOKEN", m.discoveryToken},
		{"NODE_TOKEN", m.nodeToken}, {"DEV_ALLOW_HTTP", m.devHTTP},
		{"AWG_EXPERIMENTAL_DEV", os.Getenv("AWG_EXPERIMENTAL_DEV")},
		{"WAYLANE_XRAY_STATS_READONLY", os.Getenv("WAYLANE_XRAY_STATS_READONLY")},
	} {
		if strings.ContainsAny(p[1], "\r\n") {
			envFile.Close()
			return errors.New("invalid runtime env")
		}
		if _, err = io.WriteString(envFile, p[0]+"="+p[1]+"\n"); err != nil {
			envFile.Close()
			return err
		}
	}
	if err = envFile.Close(); err != nil {
		return err
	}
	_, err = m.run(ctx, "run", "-d", "--name", nodeContainer, "--label", "waylane.component=node",
		// In the private rootless Docker daemon, container UID 0 maps to the
		// Agent's non-root outer UID 1000 and can access its 0700 state/socket.
		// Container UID 1000 instead maps to a subordinate UID and is denied.
		"--restart", "on-failure:3", "--network", "host", "--read-only", "--user", "0:0",
		"--cap-drop", "ALL", "--security-opt", "no-new-privileges",
		"--tmpfs", "/tmp:rw,nosuid,nodev,size=32m",
		"--env", "HOME=/tmp",
		"--mount", "type=bind,source="+m.stateDir+",target="+m.stateDir,
		"--mount", "type=bind,source=/run/user/1000,target=/run/user/1000",
		"--mount", "type=bind,source=/etc/waylane/host-hostname,target=/etc/waylane/host-hostname,readonly",
		"--env-file", envFile.Name(), image)
	if err != nil {
		return errors.New("Node child startup failed")
	}
	return nil
}
func (m *manager) runningNode(ctx context.Context) (bool, error) {
	status, err := m.run(ctx, "container", "inspect", "--format", "{{.State.Status}}", nodeContainer)
	if err != nil {
		return false, err
	}
	return status == "running", nil
}
func (m *manager) runningNodeImage(ctx context.Context) (string, error) {
	return m.run(ctx, "container", "inspect", "--format", "{{.Config.Image}}", nodeContainer)
}

// Compare immutable image IDs for the mutable local seed tag. Comparing
// Config.Image strings alone would silently preserve an outdated seed Node.
func (m *manager) runningNodeImageID(ctx context.Context) (string, error) {
	return m.run(ctx, "container", "inspect", "--format", "{{.Image}}", nodeContainer)
}
func (m *manager) seededImageID(ctx context.Context) (string, error) {
	return m.run(ctx, "image", "inspect", "--format", "{{.Id}}", nodeSeed)
}

// A failed update must not leave the selected release different from the
// running Node. Never modify a container without first checking its owner.
func (m *manager) restoreNode(ctx context.Context, previous string) error {
	exists, err := m.ownedNode(ctx)
	if err != nil {
		return err
	}
	if exists {
		if _, err := m.run(ctx, "rm", "-f", nodeContainer); err != nil {
			return err
		}
	}
	if err := m.startNodeStaged(ctx, previous); err != nil {
		return err
	}
	running, err := m.runningNode(ctx)
	if err != nil || !running {
		return errors.New("previous Node release did not restart")
	}
	return nil
}

func (m *manager) ensureNode(ctx context.Context) error {
	exists, err := m.ownedNode(ctx)
	if err != nil {
		return err
	}
	if exists {
		running, err := m.runningNode(ctx)
		if err != nil {
			return err
		}
		if running {
			image, err := m.runningNodeImage(ctx)
			if err != nil {
				return err
			}
			if image == m.image {
				// A running Node is never implicitly replaced when an outer
				// Agent image changes. This includes the bundled bootstrap seed.
				// Only an explicit operator release changes the Node runtime.
				return nil
			}
			// Stage the selected image before interrupting the running child.
			if err := m.stageImage(ctx, m.image); err != nil {
				return err
			}
			if image == nodeSeed && m.image == nodeSeed {
				currentID, err := m.runningNodeImageID(ctx)
				if err != nil {
					return err
				}
				seedID, err := m.seededImageID(ctx)
				if err != nil || seedID == "" {
					return errors.New("cannot inspect selected Node seed image")
				}
				if currentID == seedID {
					return nil
				}
			}
			if _, err = m.run(ctx, "stop", "--time", "10", nodeContainer); err != nil {
				return err
			}
		}
		// A child under Docker's restart policy can be in "restarting":
		// normal rm is rejected, so remove only the already owned child
		// forcefully after checking its label.
		if _, err = m.run(ctx, "rm", "-f", nodeContainer); err != nil {
			return err
		}
	}
	return m.startNode(ctx, m.image)
}

func (m *manager) updateNode(ctx context.Context, next string) error {
	return m.updateNodePrepared(ctx, next, false)
}

// Called only after executeRelease has staged the immutable image exactly
// once. Public local tests and other update callers still stage by default.
func (m *manager) updateNodeStaged(ctx context.Context, next string) error {
	return m.updateNodePrepared(ctx, next, true)
}

func (m *manager) updateNodePrepared(ctx context.Context, next string, alreadyStaged bool) error {
	if !nodeDigest.MatchString(next) {
		return errors.New("Node image digest must be immutable and from approved registry")
	}
	if next == m.image {
		return m.ensureNode(ctx)
	}
	if !alreadyStaged {
		if err := m.stageImage(ctx, next); err != nil {
			return err
		}
	}
	exists, err := m.ownedNode(ctx)
	if err != nil {
		return err
	}
	old := m.image
	if exists {
		if _, err = m.run(ctx, "stop", "--time", "10", nodeContainer); err != nil {
			return err
		}
		if _, err = m.run(ctx, "rm", nodeContainer); err != nil {
			return err
		}
	}
	if err = m.startNodeStaged(ctx, next); err != nil {
		if m.restoreNode(ctx, old) != nil {
			return errors.New("new Node failed and previous release could not be restored")
		}
		return errors.New("new Node failed; previous release restored")
	}
	running, err := m.runningNode(ctx)
	if err != nil || !running {
		if m.restoreNode(ctx, old) != nil {
			return errors.New("new Node exited and previous release could not be restored")
		}
		return errors.New("new Node exited; previous release restored")
	}
	if err = m.saveRelease(next); err != nil {
		if m.restoreNode(ctx, old) != nil {
			return errors.New("Node release persistence failed and rollback failed")
		}
		return errors.New("Node release persistence failed; previous release restored")
	}
	m.mu.Lock()
	m.image = next
	m.mu.Unlock()
	return nil
}
func (m *manager) serve(ctx context.Context) error {
	path := filepath.Join(m.stateDir, "control")
	if err := os.MkdirAll(path, 0700); err != nil {
		return err
	}
	info, err := os.Lstat(path)
	if err != nil || !info.IsDir() || info.Mode().Perm()&0077 != 0 {
		return errors.New("Agent Unix control directory must be private")
	}
	sock := filepath.Join(path, "agent.sock")
	if err := reclaimStaleAgentSocket(sock); err != nil {
		return err
	}
	l, err := net.Listen("unix", sock)
	if err != nil {
		return err
	}
	if err = os.Chmod(sock, 0600); err != nil {
		l.Close()
		return err
	}
	defer func() { l.Close(); _ = os.Remove(sock) }()
	mux := http.NewServeMux()
	mux.HandleFunc("GET /v1/node", func(w http.ResponseWriter, r *http.Request) {
		m.mu.Lock()
		defer m.mu.Unlock()
		_ = json.NewEncoder(w).Encode(map[string]any{"api_version": 1, "image": m.image})
	})
	m.registerReleaseRoutes(mux, ctx)
	server := &http.Server{Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	done := make(chan error, 1)
	go func() { done <- server.Serve(l) }()
	select {
	case <-ctx.Done():
		shutdown, cancel := context.WithTimeout(context.Background(), 4*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdown)
		return nil
	case err = <-done:
		return err
	}
}
func run(ctx context.Context) error {
	if os.Geteuid() == 0 {
		return errors.New("Agent must never run as root")
	}
	m := &manager{run: systemDocker, stateDir: os.Getenv("NODE_STATE_DIR"),
		panelURL: os.Getenv("CONTROL_PANEL_URL"), discoveryToken: os.Getenv("DISCOVERY_TOKEN"),
		nodeToken: os.Getenv("NODE_TOKEN"), devHTTP: os.Getenv("DEV_ALLOW_HTTP")}
	if m.stateDir != "/var/lib/waylane-agent" || os.Getenv("ROOTLESS_SOCKET") != dockerSocket ||
		m.panelURL == "" || ((m.discoveryToken == "") == (m.nodeToken == "")) {
		return errors.New("invalid Agent state, socket or enrollment configuration")
	}
	done, err := startDockerd(ctx)
	if err != nil {
		return err
	}
	if err = awaitDockerd(ctx, m.run, done); err != nil {
		return err
	}
	if err = m.loadRelease(); err != nil {
		return err
	}
	if err = m.loadOperation(); err != nil {
		return err
	}
	interrupted := m.operation != nil && (m.operation.Status == "queued" || m.operation.Status == "running")
	if err = m.reconcileInterruptedRelease(); err != nil {
		return err
	}
	if err = m.ensureNode(ctx); err != nil {
		return err
	}
	if interrupted {
		// Let the restored Node prove a NEW authenticated WSS connection.
		// A running child alone must not be called a successful rollback.
		go m.awaitRollbackReady(ctx, "agent_restarted_during_update")
	}
	return m.serve(ctx)
}
func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := run(ctx); err != nil && ctx.Err() == nil {
		log.Fatalf("WayLane Agent: %v", err)
	}
}
