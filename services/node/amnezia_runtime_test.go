package main

import (
	"context"
	"errors"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

type fakeAWGDocker struct {
	calls  [][]string
	owned  bool
	failAt string
}

func (f *fakeAWGDocker) Run(_ context.Context, args ...string) (string, error) {
	f.calls = append(f.calls, append([]string(nil), args...))
	if len(args) == 0 {
		return "", errors.New("no args")
	}
	if args[0] == f.failAt {
		return "", errors.New("fake failure")
	}
	if args[0] == "run" {
		f.owned = true
	}
	if args[0] == "inspect" && len(args) == 2 {
		return "", errors.New("not found")
	}
	if args[0] == "inspect" && len(args) == 4 {
		if f.owned {
			return "awg/default", nil
		}
		return "", errors.New("not owned")
	}
	return "", nil
}
func (f *fakeAWGDocker) has(args ...string) bool {
	for _, call := range f.calls {
		if len(call) < len(args) {
			continue
		}
		match := true
		for i, s := range args {
			if call[i] != s {
				match = false
				break
			}
		}
		if match {
			return true
		}
	}
	return false
}

func TestAWGChildRootlessLifecycleCommandContract(t *testing.T) {
	if runtime.GOOS != "linux" || runtime.GOARCH != "amd64" {
		t.Skip("official pinned image only supports Linux amd64")
	}
	fake := &fakeAWGDocker{}
	sent := 0
	child := awgChildRuntime{runner: fake, stateDir: t.TempDir(), waitSocket: func(ctx context.Context, p string) error {
		if !strings.HasSuffix(p, "awg0.sock") {
			t.Fatal("wrong UAPI socket")
		}
		return nil
	}, applyUAPI: func(_ context.Context, _ string, request []byte) ([]string, error) {
		sent++
		if !strings.Contains(string(request), "private_key=") {
			t.Fatal("UAPI key not provided")
		}
		return nil, nil
	}}
	cfg := awgTestConfig()
	if err := child.Start(context.Background(), cfg); err != nil {
		t.Fatal(err)
	}
	if sent != 1 {
		t.Fatal("UAPI was not configured")
	}
	if !fake.has("pull", awgImage) || !fake.has("run", "-d", "--name", awgChildName) {
		t.Fatalf("missing child lifecycle calls: %v", fake.calls)
	}
	if !fake.has("exec", awgChildName, "ip", "address", "add", "10.90.0.1/24", "dev", "awg0") || !fake.has("exec", awgChildName, "ip", "link", "set", "dev", "awg0", "up") {
		t.Fatal("device address or link setup missing")
	}
	for _, call := range fake.calls {
		joined := strings.Join(call, " ")
		if strings.Contains(joined, cfg.PrivateKey) || strings.Contains(joined, cfg.Peers[0].PreSharedKey) {
			t.Fatal("private key in docker arguments")
		}
		if call[0] == "run" {
			for _, required := range []string{"--cap-add NET_ADMIN", "--device /dev/net/tun:/dev/net/tun", "--read-only", "--network host", awgImage} {
				if !strings.Contains(joined, required) {
					t.Fatalf("missing run protection %q: %s", required, joined)
				}
			}
		}
	}
}
func TestAWGChildInvalidConfigNeverMutatesRuntime(t *testing.T) {
	fake := &fakeAWGDocker{}
	child := awgChildRuntime{runner: fake, stateDir: t.TempDir()}
	cfg := awgTestConfig()
	cfg.Amnezia.I1 = "<t>\nprivate_key=bad"
	if err := child.Start(context.Background(), cfg); err == nil {
		t.Fatal("invalid config accepted")
	}
	if len(fake.calls) != 0 {
		t.Fatal("invalid AWG config mutated Docker")
	}
}
func TestAWGChildStartFailureCleansOnlyItsContainer(t *testing.T) {
	if runtime.GOOS != "linux" || runtime.GOARCH != "amd64" {
		t.Skip("official pinned image only supports Linux amd64")
	}
	fake := &fakeAWGDocker{}
	child := awgChildRuntime{runner: fake, stateDir: t.TempDir(), waitSocket: func(context.Context, string) error { return errors.New("timed out") }}
	err := child.Start(context.Background(), awgTestConfig())
	if err == nil {
		t.Fatal("AWG with no UAPI socket must fail")
	}
	if !fake.has("rm", "-f", awgChildName) {
		t.Fatal("new failed child not removed")
	}
}
func TestAWGChildStopRequiresOwnedLabel(t *testing.T) {
	fake := &fakeAWGDocker{}
	child := awgChildRuntime{runner: fake, stateDir: t.TempDir()}
	if err := child.Stop(context.Background()); err == nil {
		t.Fatal("unowned child stop allowed")
	}
	if fake.has("stop") || fake.has("rm") {
		t.Fatal("unowned container was touched")
	}
	fake.owned = true
	if err := child.Stop(context.Background()); err != nil {
		t.Fatal(err)
	}
	if !fake.has("stop", "--time", "5", awgChildName) || !fake.has("rm", awgChildName) {
		t.Fatal("owned child stop/remove not executed")
	}
}
func TestAWGChildPeerReconfigurePreservesNetwork(t *testing.T) {
	fake := &fakeAWGDocker{owned: true}
	requests := 0
	child := awgChildRuntime{runner: fake, stateDir: t.TempDir(), applyUAPI: func(_ context.Context, p string, _ []byte) ([]string, error) {
		requests++
		if filepath.Base(p) != "awg0.sock" {
			t.Fatal("wrong private socket")
		}
		return nil, nil
	}}
	before := awgTestConfig()
	after := awgTestConfig()
	after.Peers[0].AllowedIPs = []string{"10.90.0.3/32"}
	if err := child.Reconfigure(context.Background(), before, after); err != nil {
		t.Fatal(err)
	}
	if requests != 1 {
		t.Fatal("expected live AWG UAPI peer update")
	}
	after.Address = []string{"10.91.0.1/24"}
	if err := child.Reconfigure(context.Background(), before, after); err == nil {
		t.Fatal("unexpected network reconfiguration")
	}
	if requests != 1 {
		t.Fatal("unsupported network update mutated core")
	}
}
