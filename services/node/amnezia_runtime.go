package main

// Experimental AmneziaWG-Go child runtime. It remains intentionally absent
// from the production coreDriver map and capability advertisement until the
// backend has per-core durable state and RootlessKit TUN/UDP is accepted.

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

const awgChildName = "waylane-awg-default"
const awgImage = "docker.io/amneziavpn/amneziawg-go@sha256:c68009d33df3aef4654db72bf1a7880cfa0a631fe866c50d9ae3dd34ddd7c13a"
const awgCoreLabel = "waylane.core.id=awg/default"

var errAWGRequiresRestart = errors.New("AWG network change requires explicit stop and deploy")

// awgCommandRunner is deliberately mockable; subprocess arguments must never
// contain server/peer private material. There is no shell interpolation.
type awgCommandRunner interface {
	Run(ctx context.Context, args ...string) (string, error)
}

type privateDockerRunner struct{ socket string }

func (r privateDockerRunner) Run(ctx context.Context, args ...string) (string, error) {
	if r.socket != "/run/user/1000/docker.sock" {
		return "", errors.New("private Docker socket required")
	}
	argv := append([]string{"--host", "unix://" + r.socket}, args...)
	cmd := exec.CommandContext(ctx, "docker", argv...)
	raw, err := cmd.Output()
	if err != nil {
		return "", errors.New("private Docker operation failed")
	}
	if len(raw) > 4096 {
		return "", errors.New("private Docker output too large")
	}
	return strings.TrimSpace(string(raw)), nil
}

type awgChildRuntime struct {
	runner     awgCommandRunner
	stateDir   string
	waitSocket func(context.Context, string) error
	applyUAPI  func(context.Context, string, []byte) ([]string, error)
}

func newAWGChildRuntime(stateDir, socket string) awgChildRuntime {
	return awgChildRuntime{runner: privateDockerRunner{socket: socket}, stateDir: stateDir,
		waitSocket: awaitAWGSocket, applyUAPI: awgPrivateUAPI}
}

func (r awgChildRuntime) privateSocketDir() (string, error) {
	if !filepath.IsAbs(r.stateDir) || filepath.Clean(r.stateDir) != r.stateDir {
		return "", errors.New("absolute clean Node state directory required")
	}
	base := filepath.Join(r.stateDir, "awg", "uapi")
	if err := os.MkdirAll(base, 0700); err != nil {
		return "", err
	}
	info, err := os.Lstat(base)
	if err != nil || !info.IsDir() || info.Mode().Perm()&0077 != 0 {
		return "", errors.New("AWG UAPI directory must be private")
	}
	return base, nil
}

func awaitAWGSocket(ctx context.Context, socket string) error {
	ticker := time.NewTicker(150 * time.Millisecond)
	defer ticker.Stop()
	for {
		stat, err := os.Lstat(socket)
		if err == nil {
			if stat.Mode()&os.ModeSocket != 0 {
				return nil
			}
			return errors.New("AWG UAPI path is not a socket")
		}
		if !errors.Is(err, os.ErrNotExist) {
			return errors.New("AWG UAPI socket stat failed")
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
	}
}

func (r awgChildRuntime) run(ctx context.Context, args ...string) (string, error) {
	return r.runner.Run(ctx, args...)
}

func (r awgChildRuntime) verifyOwned(ctx context.Context) error {
	label, err := r.run(ctx, "inspect", "--format", "{{index .Config.Labels \"waylane.core.id\"}}", awgChildName)
	if err != nil || label != "awg/default" {
		return errors.New("AWG container missing or not Node-owned")
	}
	return nil
}

// Start creates a single, labeled, least-privileged AWG child and configures
// it via private UAPI. It is deliberately not invoked by production control.
// The child shares only the rootless daemon's network namespace, NOT an
// assumed externally reachable UDP port. Traffic is unaccepted until tested.
func (r awgChildRuntime) Start(ctx context.Context, config awgWireConfig) (err error) {
	if runtime.GOOS != "linux" || runtime.GOARCH != "amd64" {
		return errors.New("pinned AWG-Go image only supports linux/amd64")
	}
	request, err := awgUAPISet(config)
	if err != nil {
		return err
	}
	dir, err := r.privateSocketDir()
	if err != nil {
		return err
	}
	socket := filepath.Join(dir, config.InterfaceName+".sock")
	if info, statErr := os.Lstat(socket); statErr == nil || !errors.Is(statErr, os.ErrNotExist) {
		if statErr == nil && info.Mode()&os.ModeSocket != 0 {
			return errors.New("stale or active AWG UAPI socket must be reconciled first")
		}
		return errors.New("invalid AWG UAPI socket path")
	}
	// Never replace a running container whose ownership we have not verified.
	if _, err = r.run(ctx, "inspect", awgChildName); err == nil {
		return errors.New("AWG child already exists; explicit reconciliation required")
	}
	if _, err = r.run(ctx, "pull", awgImage); err != nil {
		return err
	}
	args := []string{"run", "-d", "--name", awgChildName,
		"--label", awgCoreLabel, "--restart", "unless-stopped",
		"--network", "host", "--read-only", "--cap-drop", "ALL", "--cap-add", "NET_ADMIN",
		"--device", "/dev/net/tun:/dev/net/tun", "--security-opt", "no-new-privileges",
		"--user", "0:0", "--tmpfs", "/tmp:rw,nosuid,nodev,size=16m",
		"--mount", "type=bind,source=" + dir + ",target=/var/run/amneziawg",
		awgImage, "/usr/bin/amneziawg-go", "-f", config.InterfaceName}
	if _, err = r.run(ctx, args...); err != nil {
		return err
	}
	defer func() {
		if err != nil {
			cleanupCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			// A failed startup must never remove a differently owned Core.
			if r.verifyOwned(cleanupCtx) == nil {
				_, _ = r.run(cleanupCtx, "rm", "-f", awgChildName)
			}
		}
	}()
	waitCtx, cancel := context.WithTimeout(ctx, 12*time.Second)
	defer cancel()
	if err = r.waitSocket(waitCtx, socket); err != nil {
		return errors.New("AWG UAPI did not become ready")
	}
	if _, err = r.applyUAPI(waitCtx, socket, request); err != nil {
		return err
	}
	for _, address := range config.Address {
		if _, err = r.run(ctx, "exec", awgChildName, "ip", "address", "add", address, "dev", config.InterfaceName); err != nil {
			return err
		}
	}
	if _, err = r.run(ctx, "exec", awgChildName, "ip", "link", "set", "dev", config.InterfaceName, "up"); err != nil {
		return err
	}
	return nil
}

// Healthy checks both Docker ownership/running state and the mounted private
// UAPI socket. Source checks cannot substitute for a real UDP peer handshake.
func (r awgChildRuntime) Healthy(ctx context.Context) error {
	if err := r.verifyOwned(ctx); err != nil {
		return err
	}
	running, err := r.run(ctx, "inspect", "--format", "{{.State.Running}}", awgChildName)
	if err != nil || running != "true" {
		return errors.New("AWG child is not running")
	}
	dir, err := r.privateSocketDir()
	if err != nil {
		return err
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return errors.New("AWG private socket directory unavailable")
	}
	found := false
	for _, entry := range entries {
		if !strings.HasSuffix(entry.Name(), ".sock") {
			continue
		}
		info, e := os.Lstat(filepath.Join(dir, entry.Name()))
		if e == nil && info.Mode()&os.ModeSocket != 0 {
			found = true
			break
		}
	}
	if !found {
		return errors.New("AWG private UAPI socket unavailable")
	}
	return nil
}

// Stop only touches the Node-labeled child and never a host/core process.
func (r awgChildRuntime) Stop(ctx context.Context) error {
	if err := r.verifyOwned(ctx); err != nil {
		return err
	}
	if _, err := r.run(ctx, "stop", "--time", "5", awgChildName); err != nil {
		return err
	}
	_, err := r.run(ctx, "rm", awgChildName)
	return err
}

// Reconfigure applies UAPI changes to the same verified child without
// touching Xray or other cores. It rejects interface renames and address
// changes: these require a separately reviewed stop/deploy transaction.
func (r awgChildRuntime) Reconfigure(ctx context.Context, original, next awgWireConfig) error {
	if next.InterfaceName != original.InterfaceName || next.ListenPort != original.ListenPort ||
		len(next.Address) != len(original.Address) {
		return errAWGRequiresRestart
	}
	for i, v := range next.Address {
		if v != original.Address[i] {
			return errAWGRequiresRestart
		}
	}
	if err := r.verifyOwned(ctx); err != nil {
		return err
	}
	request, err := awgUAPISet(next)
	if err != nil {
		return err
	}
	dir, err := r.privateSocketDir()
	if err != nil {
		return err
	}
	_, err = r.applyUAPI(ctx, filepath.Join(dir, next.InterfaceName+".sock"), request)
	return err
}
