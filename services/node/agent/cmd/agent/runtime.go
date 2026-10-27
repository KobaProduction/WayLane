package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"os"
	"os/exec"
	"syscall"
	"time"
)

// startRootlessEngine is owned by the WayLane Agent, not another Compose
// service. Never fall back to a system/rootful Docker daemon.
func startRootlessEngine(ctx context.Context, socket string) (<-chan error, error) {
	if os.Geteuid() == 0 {
		return nil, errors.New("rootless daemon must run under an unprivileged user")
	}
	if socket != "/run/user/1000/docker.sock" {
		return nil, fmt.Errorf("unexpected private Docker socket path")
	}
	cmd := exec.CommandContext(ctx, "dockerd-entrypoint.sh",
		"dockerd",
		"--host=unix://"+socket,
		"--storage-driver=vfs",
		"--iptables=false",
		"--ip6tables=false",
		"--bridge=none",
		"--tls=false",
	)
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
		return nil, fmt.Errorf("failed to spawn embedded rootless Docker: %w", err)
	}
	log.Printf("embedded rootless Docker started, pid=%d", cmd.Process.Pid)
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	return done, nil
}

func awaitRootlessEngine(ctx context.Context, socket string, done <-chan error) error {
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	timeout := time.NewTimer(65 * time.Second)
	defer timeout.Stop()
	for {
		if err := checkRootlessDocker(ctx, socket); err == nil {
			return nil
		}
		select {
		case err := <-done:
			return fmt.Errorf("embedded rootless Docker exited during startup: %w", err)
		case <-ctx.Done():
			return ctx.Err()
		case <-timeout.C:
			return errors.New("embedded rootless Docker did not become ready")
		case <-ticker.C:
		}
	}
}
