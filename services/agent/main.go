package main

// Stable Agent: owns an embedded rootless Docker daemon, installs a replaceable
// Node container, and exposes a fixed local Unix-socket management API.
// The Node (not Agent) owns the existing binary WSS and child VPN Cores.
import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
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
		if _, err := m.run(ctx, "image", "inspect", nodeSeed); err == nil {
			return nil
		}
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
		"--restart", "unless-stopped", "--network", "host", "--read-only", "--user", "1000:1000",
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
	status, err := m.run(ctx, "container", "inspect", "--format", "{{.State.Running}}", nodeContainer)
	if err != nil {
		return false, err
	}
	return status == "true", nil
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
			return nil
		}
		if _, err = m.run(ctx, "rm", nodeContainer); err != nil {
			return err
		}
	}
	return m.startNode(ctx, m.image)
}
func (m *manager) updateNode(ctx context.Context, next string) error {
	if !nodeDigest.MatchString(next) {
		return errors.New("Node image digest must be immutable and from approved registry")
	}
	if next == m.image {
		return nil
	}
	if err := m.stageImage(ctx, next); err != nil {
		return err
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
	if err = m.startNode(ctx, next); err != nil {
		_ = m.startNode(ctx, old)
		return err
	}
	running, err := m.runningNode(ctx)
	if err != nil || !running {
		_, _ = m.run(ctx, "rm", "-f", nodeContainer)
		_ = m.startNode(ctx, old)
		return errors.New("new Node exited; rollback attempted")
	}
	if err = m.saveRelease(next); err != nil {
		return err
	}
	m.image = next
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
	if _, err := os.Lstat(sock); err == nil {
		return errors.New("Agent Unix socket already occupied")
	} else if !errors.Is(err, os.ErrNotExist) {
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
	mux.HandleFunc("PUT /v1/node/release", func(w http.ResponseWriter, r *http.Request) {
		m.mu.Lock()
		defer m.mu.Unlock()
		raw, err := io.ReadAll(io.LimitReader(r.Body, 4096))
		if err != nil {
			http.Error(w, "invalid request", 400)
			return
		}
		var doc struct {
			APIVersion int    `json:"api_version"`
			Image      string `json:"image"`
		}
		dec := json.NewDecoder(bytes.NewReader(raw))
		dec.DisallowUnknownFields()
		if err = dec.Decode(&doc); err != nil || doc.APIVersion != 1 || !nodeDigest.MatchString(doc.Image) {
			http.Error(w, "invalid pinned Node release", 400)
			return
		}
		// The requester may be the Node being replaced. Its HTTP disconnect
		// must not cancel the Agent-owned replacement transaction.
		updateCtx, cancel := context.WithTimeout(ctx, 3*time.Minute)
		defer cancel()
		if err = m.updateNode(updateCtx, doc.Image); err != nil {
			http.Error(w, "Node update failed", 409)
			return
		}
		w.WriteHeader(204)
	})
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
	if err = m.ensureNode(ctx); err != nil {
		return err
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
