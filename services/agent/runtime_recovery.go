package main

// A private RootlessKit runtime directory is mounted from an installation-
// scoped volume. Abrupt termination can strand containerd's PID and Unix
// sockets, even though the container PID namespace has been destroyed.
// Reclaim only demonstrably dead, owned endpoints before starting dockerd.
import (
	"errors"
	"fmt"
	"log"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

func inspectOwnedArtifact(path string, kind os.FileMode) (bool, error) {
	info, err := os.Lstat(path)
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	owner, ok := info.Sys().(*syscall.Stat_t)
	if !ok || owner.Uid != uint32(os.Geteuid()) {
		return false, errors.New("private runtime artifact owner mismatch")
	}
	if kind == os.ModeSocket {
		if info.Mode()&os.ModeSocket == 0 {
			return false, errors.New("private runtime socket has unexpected file type")
		}
	} else if !info.Mode().IsRegular() {
		return false, errors.New("private runtime pid file has unexpected file type")
	}
	return true, nil
}

func socketIsListening(path string) (bool, error) {
	conn, err := net.DialTimeout("unix", path, 250*time.Millisecond)
	if err == nil {
		_ = conn.Close()
		return true, nil
	}
	if errors.Is(err, syscall.ECONNREFUSED) || errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	return false, errors.New("cannot establish whether private Unix socket has an active listener")
}

func reclaimStaleAgentSocket(path string) error {
	found, err := inspectOwnedArtifact(path, os.ModeSocket)
	if err != nil || !found {
		return err
	}
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if info.Mode().Perm()&0077 != 0 {
		return errors.New("Agent control socket must remain private")
	}
	active, err := socketIsListening(path)
	if err != nil {
		return err
	}
	if active {
		return errors.New("Agent Unix socket already occupied")
	}
	if err := os.Remove(path); err != nil {
		return err
	}
	log.Print("WayLane Agent: reclaimed abandoned private control socket")
	return nil
}

func reclaimStaleContainerdRuntime(dir string) error {
	info, err := os.Lstat(dir)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	owner, ok := info.Sys().(*syscall.Stat_t)
	if !ok || owner.Uid != uint32(os.Geteuid()) || !info.IsDir() || info.Mode().Perm()&0077 != 0 {
		return errors.New("containerd runtime directory must be privately owned")
	}
	pid := filepath.Join(dir, "containerd.pid")
	found, err := inspectOwnedArtifact(pid, 0)
	if err != nil {
		return err
	}
	toRemove := []string{}
	if found {
		raw, err := os.ReadFile(pid)
		if err != nil || len(raw) > 16 {
			return errors.New("invalid private containerd pid file")
		}
		number, err := strconv.Atoi(strings.TrimSpace(string(raw)))
		if err != nil || number < 1 {
			return errors.New("invalid private containerd pid value")
		}
		toRemove = append(toRemove, pid)
	}
	// The recorded PID belongs to RootlessKit's *former* PID namespace. A
	// host/Agent kill(pid,0) would be misleading; probe the actual sockets.
	// A previously aborted cleanup may have removed the PID but left sockets.
	targets := []string{"containerd.sock.ttrpc", "containerd-debug.sock"}
	for _, name := range targets {
		path := filepath.Join(dir, name)
		exists, err := inspectOwnedArtifact(path, os.ModeSocket)
		if err != nil {
			return err
		}
		if !exists {
			continue
		}
		active, err := socketIsListening(path)
		if err != nil {
			return err
		}
		if active {
			return fmt.Errorf("private containerd endpoint %s is still serving", name)
		}
		toRemove = append(toRemove, path)
	}
	if len(toRemove) == 0 {
		return nil
	}
	for _, path := range toRemove {
		if err := os.Remove(path); err != nil {
			return err
		}
	}
	log.Print("WayLane Agent: reclaimed abandoned private containerd runtime endpoints")
	return nil
}
