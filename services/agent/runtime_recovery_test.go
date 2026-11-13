package main

import (
	"net"
	"os"
	"path/filepath"
	"testing"
)

func staleTestSocket(t *testing.T, path string) {
	t.Helper()
	listener, err := net.Listen("unix", path)
	if err != nil {
		t.Fatal(err)
	}
	owned := listener.(*net.UnixListener)
	owned.SetUnlinkOnClose(false)
	if err := os.Chmod(path, 0600); err != nil {
		t.Fatal(err)
	}
	if err := owned.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestReclaimsOnlyStalePrivateAgentSocket(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.sock")
	if err := reclaimStaleAgentSocket(path); err != nil {
		t.Fatal(err)
	}
	live, err := net.Listen("unix", path)
	if err != nil {
		t.Fatal(err)
	}
	owned := live.(*net.UnixListener)
	owned.SetUnlinkOnClose(false)
	if err := os.Chmod(path, 0600); err != nil {
		t.Fatal(err)
	}
	if err := reclaimStaleAgentSocket(path); err == nil {
		t.Fatal("live Agent socket was accepted for removal")
	}
	if _, err := os.Lstat(path); err != nil {
		t.Fatal("live Agent socket deleted", err)
	}
	if err := owned.Close(); err != nil {
		t.Fatal(err)
	}
	if err := reclaimStaleAgentSocket(path); err != nil {
		t.Fatal("stale Agent socket not removed", err)
	}
	if _, err := os.Lstat(path); !os.IsNotExist(err) {
		t.Fatal("stale socket remains", err)
	}
}

func TestAgentSocketRejectsFilesAndSymlinks(t *testing.T) {
	dir := t.TempDir()
	file := filepath.Join(dir, "existing.txt")
	if err := os.WriteFile(file, []byte("do not modify"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := reclaimStaleAgentSocket(file); err == nil {
		t.Fatal("regular file was treated as a socket")
	}
	link := filepath.Join(dir, "agent.sock")
	if err := os.Symlink(file, link); err != nil {
		t.Fatal(err)
	}
	if err := reclaimStaleAgentSocket(link); err == nil {
		t.Fatal("symlink was treated as an owned socket")
	}
	if _, err := os.Stat(file); err != nil {
		t.Fatal("target was damaged", err)
	}
}

func TestReclaimsOnlyDeadRootlessContainerdArtifacts(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "docker", "containerd")
	if err := os.MkdirAll(dir, 0700); err != nil {
		t.Fatal(err)
	}
	pid := filepath.Join(dir, "containerd.pid")
	if err := os.WriteFile(pid, []byte("122\n"), 0644); err != nil {
		t.Fatal(err)
	}
	staleTestSocket(t, filepath.Join(dir, "containerd.sock.ttrpc"))
	staleTestSocket(t, filepath.Join(dir, "containerd-debug.sock"))
	sentinel := filepath.Join(dir, "containerd.toml")
	if err := os.WriteFile(sentinel, []byte("state remains intact"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := reclaimStaleContainerdRuntime(dir); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"containerd.pid", "containerd.sock.ttrpc", "containerd-debug.sock"} {
		if _, err := os.Lstat(filepath.Join(dir, name)); !os.IsNotExist(err) {
			t.Fatalf("stale %s not removed: %v", name, err)
		}
	}
	got, err := os.ReadFile(sentinel)
	if err != nil || string(got) != "state remains intact" {
		t.Fatal("persistent state was modified", err)
	}
}

func TestPreservesActiveAndUntrustedContainerdRuntime(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "docker", "containerd")
	if err := os.MkdirAll(dir, 0700); err != nil {
		t.Fatal(err)
	}
	pid := filepath.Join(dir, "containerd.pid")
	if err := os.WriteFile(pid, []byte("122"), 0600); err != nil {
		t.Fatal(err)
	}
	socket := filepath.Join(dir, "containerd.sock.ttrpc")
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	if err := reclaimStaleContainerdRuntime(dir); err == nil {
		t.Fatal("live containerd was disrupted")
	}
	if _, err := os.Lstat(pid); err != nil {
		t.Fatal("live pidfile was removed", err)
	}
	listener.Close()
	if err := os.WriteFile(socket, []byte("not a socket"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := reclaimStaleContainerdRuntime(dir); err == nil {
		t.Fatal("spoofed runtime socket was accepted")
	}
	if _, err := os.Lstat(pid); err != nil {
		t.Fatal("unsafe cleanup removed pidfile", err)
	}
}

func TestRecoversDeadContainerdSocketWithoutPidFile(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "docker", "containerd")
	if err := os.MkdirAll(dir, 0700); err != nil {
		t.Fatal(err)
	}
	socket := filepath.Join(dir, "containerd.sock.ttrpc")
	staleTestSocket(t, socket)
	if err := reclaimStaleContainerdRuntime(dir); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Lstat(socket); !os.IsNotExist(err) {
		t.Fatal("stale socket not removed", err)
	}
}
