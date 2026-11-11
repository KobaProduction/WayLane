package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const testDigest = "ghcr.io/kobaproduction/waylane-node@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

type fakeDocker struct {
	calls   [][]string
	exists  bool
	owned   bool
	running bool
	fail    string
}

func (f *fakeDocker) Run(_ context.Context, args ...string) (string, error) {
	f.calls = append(f.calls, append([]string(nil), args...))
	if len(args) == 0 {
		return "", errors.New("no args")
	}
	if args[0] == f.fail {
		return "", errors.New("synthetic Docker failure")
	}
	switch args[0] {
	case "info":
		return `["name=rootless"]`, nil
	case "image":
		return "", nil
	case "pull":
		return "", nil
	case "build":
		return "", nil
	case "container":
		if !f.exists {
			return "", errors.New("missing")
		}
		if len(args) > 3 && strings.Contains(args[3], "waylane.component") {
			if !f.owned {
				return "unrelated", nil
			}
			return "node", nil
		}
		if f.running {
			return "true", nil
		}
		return "false", nil
	case "run":
		if f.fail == "new-image" && args[len(args)-1] == testDigest {
			return "", errors.New("synthetic replacement startup failure")
		}
		f.exists = true
		f.owned = true
		f.running = true
		return "synthetic-container-id", nil
	case "stop":
		f.running = false
		return "", nil
	case "rm":
		f.exists = false
		f.running = false
		return "", nil
	}
	return "", nil
}
func (f *fakeDocker) has(head string) bool {
	for _, c := range f.calls {
		if len(c) > 0 && c[0] == head {
			return true
		}
	}
	return false
}
func TestAgentRejectsUnapprovedNodeImage(t *testing.T) {
	for _, v := range []string{"", nodeSeed, "ubuntu:latest", "ghcr.io/kobaproduction/waylane-node:latest",
		"ghcr.io/another/repo@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
		"ghcr.io/kobaproduction/waylane-node@sha256:ABCDEF"} {
		if nodeDigest.MatchString(v) {
			t.Fatalf("mutable/unapproved image accepted: %s", v)
		}
	}
	if !nodeDigest.MatchString(testDigest) {
		t.Fatal("approved immutable digest rejected")
	}
}
func TestAgentReleaseStaysPrivateAndRecovers(t *testing.T) {
	m := &manager{stateDir: t.TempDir()}
	if err := m.loadRelease(); err != nil || m.image != nodeSeed {
		t.Fatal("initial seed not selected", err)
	}
	if err := m.saveRelease(testDigest); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(m.releasePath())
	if err != nil || info.Mode().Perm() != 0600 {
		t.Fatal("release not private", err)
	}
	m.image = ""
	if err := m.loadRelease(); err != nil || m.image != testDigest {
		t.Fatal("persistent release mismatch", err)
	}
	if err := m.saveRelease("example:latest"); err == nil {
		t.Fatal("mutable image persisted")
	}
}
func TestStableAgentStartsNodeOnPrivateRootlessDaemon(t *testing.T) {
	docker := &fakeDocker{}
	m := &manager{run: docker.Run, stateDir: t.TempDir(), panelURL: "http://host.docker.internal:18650",
		discoveryToken: "wl_disc_synthetic", devHTTP: "true", image: nodeSeed}
	if err := m.ensureNode(context.Background()); err != nil {
		t.Fatal(err)
	}
	if !docker.has("run") || !docker.has("image") {
		t.Fatal("Node seed installation did not run")
	}
	if err := m.ensureNode(context.Background()); err != nil {
		t.Fatal(err)
	}
	count := 0
	for _, args := range docker.calls {
		if args[0] != "run" {
			continue
		}
		count++
		joined := strings.Join(args, " ")
		for _, required := range []string{"--network host", "--env-file", "waylane.component=node",
			"/run/user/1000", "waylane-node-seed:bootstrap", "--cap-drop ALL"} {
			if !strings.Contains(joined, required) {
				t.Fatalf("Node lacks expected isolation %q: %s", required, joined)
			}
		}
		if strings.Contains(joined, m.discoveryToken) {
			t.Fatal("Node secret leaked to CLI arguments")
		}
	}
	if count != 1 {
		t.Fatal("running Node replaced on normal Agent restart")
	}
}
func TestAgentNeverDeletesUnownedNodeContainer(t *testing.T) {
	docker := &fakeDocker{exists: true, owned: false, running: true}
	m := &manager{run: docker.Run, stateDir: t.TempDir(), image: nodeSeed}
	if err := m.ensureNode(context.Background()); err == nil {
		t.Fatal("unowned container accepted")
	}
	if docker.has("stop") || docker.has("rm") {
		t.Fatal("unowned container changed")
	}
}
func TestNodeReleaseUpdateDoesNotRestartAgent(t *testing.T) {
	docker := &fakeDocker{exists: true, owned: true, running: true}
	dir := t.TempDir()
	m := &manager{run: docker.Run, stateDir: dir, panelURL: "https://panel.invalid", nodeToken: "wl_node_synthetic", image: nodeSeed}
	if err := m.updateNode(context.Background(), testDigest); err != nil {
		t.Fatal(err)
	}
	if m.image != testDigest {
		t.Fatal("new pinned image not set")
	}
	if !docker.has("pull") || !docker.has("stop") || !docker.has("run") {
		t.Fatal("Node was not replaced independently")
	}
	if _, err := os.Stat(filepath.Join(dir, "node-release.json")); err != nil {
		t.Fatal("desired Node digest not persisted")
	}
}

func TestReleaseSymlinkIsRejected(t *testing.T) {
	dir := t.TempDir()
	real := filepath.Join(dir, "real.json")
	if err := os.WriteFile(real, []byte("{}"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(real, filepath.Join(dir, "node-release.json")); err != nil {
		t.Fatal(err)
	}
	manager := &manager{stateDir: dir}
	if err := manager.loadRelease(); err == nil {
		t.Fatal("symlinked Node release allowed")
	}
}
func TestReleaseWorldReadableFileRejected(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "node-release.json"), []byte("{}"), 0644); err != nil {
		t.Fatal(err)
	}
	manager := &manager{stateDir: dir}
	if err := manager.loadRelease(); err == nil {
		t.Fatal("public Node release allowed")
	}
}

func TestAgentUnixAPIIsPrivateAndVersioned(t *testing.T) {
	dir := t.TempDir()
	fake := &fakeDocker{exists: true, owned: true, running: true}
	mgr := &manager{run: fake.Run, stateDir: dir, panelURL: "https://panel.invalid", nodeToken: "synthetic", image: nodeSeed}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	finished := make(chan error, 1)
	go func() { finished <- mgr.serve(ctx) }()
	socket := filepath.Join(dir, "control", "agent.sock")
	var info os.FileInfo
	for i := 0; i < 100; i++ {
		var err error
		info, err = os.Lstat(socket)
		if err == nil {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if info == nil || info.Mode().Perm() != 0600 {
		t.Fatal("Agent control API socket is not private")
	}
	transport := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "unix", socket)
	}}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: 3 * time.Second}
	res, err := client.Get("http://agent/v1/node")
	if err != nil {
		t.Fatal(err)
	}
	var status map[string]any
	if err = json.NewDecoder(res.Body).Decode(&status); err != nil {
		t.Fatal(err)
	}
	res.Body.Close()
	if status["api_version"] != float64(1) || status["image"] != nodeSeed {
		t.Fatalf("Agent API mismatch: %+v", status)
	}
	body := []byte("{\"api_version\":1,\"image\":\"mutable:latest\"}")
	req, _ := http.NewRequest(http.MethodPut, "http://agent/v1/node/release", bytes.NewReader(body))
	res, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	res.Body.Close()
	if res.StatusCode != 400 || fake.has("pull") {
		t.Fatal("unapproved Node image was accepted")
	}
	cancel()
	select {
	case err = <-finished:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("Agent Unix API did not stop")
	}
}

func TestAgentRollsBackWhenNewNodeCannotStart(t *testing.T) {
	fake := &fakeDocker{exists: true, owned: true, running: true, fail: "new-image"}
	m := &manager{run: fake.Run, stateDir: t.TempDir(), panelURL: "https://panel.invalid",
		nodeToken: "synthetic", image: nodeSeed}
	if err := m.updateNode(context.Background(), testDigest); err == nil {
		t.Fatal("failed replacement accepted")
	}
	if m.image != nodeSeed || !fake.running {
		t.Fatal("failed new Node lost running previous release")
	}
	restored := false
	for _, call := range fake.calls {
		if len(call) > 0 && call[0] == "run" && call[len(call)-1] == nodeSeed {
			restored = true
		}
	}
	if !restored {
		t.Fatal("no rollback to pinned previous Node image was attempted")
	}
}

func TestDockerCLIUsesWritablePrivateConfigEvenWithReadOnlyHome(t *testing.T) {
	path := t.TempDir()
	docker := filepath.Join(path, "docker")
	content := []byte("#!/bin/sh\necho \"$DOCKER_CONFIG\"\n")
	if err := os.WriteFile(docker, content, 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", path+":"+os.Getenv("PATH"))
	t.Setenv("DOCKER_CONFIG", "/home/rootless/.docker")
	got, err := systemDocker(context.Background(), "info")
	if err != nil {
		t.Fatal(err)
	}
	if got != "/tmp/waylane-docker-cli" {
		t.Fatalf("Docker CLI still writes to readonly home: %q", got)
	}
	info, err := os.Stat(got)
	if err != nil || !info.IsDir() || info.Mode().Perm()&0077 != 0 {
		t.Fatal("Docker CLI config directory not private", err)
	}
}
