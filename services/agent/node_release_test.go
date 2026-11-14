package main

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const opTestID = "0192b532-184f-41bc-9952-78e15b14cc55"

func awaitRelease(t *testing.T, m *manager, wanted string) nodeReleaseOperation {
	t.Helper()
	until := time.Now().Add(5 * time.Second)
	for time.Now().Before(until) {
		value, ok := m.releaseSnapshot()
		if ok && (value.Phase == wanted || value.Status == wanted) {
			return value
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("release did not reach %s", wanted)
	return nodeReleaseOperation{}
}

func TestStableAgentAsyncReleasePersistsProgressAndRequiresWSSReadiness(t *testing.T) {
	fake := &fakeDocker{exists: true, owned: true, running: true, image: nodeSeed}
	m := &manager{run: fake.Run, stateDir: t.TempDir(), image: nodeSeed, panelURL: "https://panel.invalid"}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	started, err := m.startRelease(ctx, opTestID, testDigest)
	if err != nil || started.Status != "queued" || started.Progress != 0 {
		t.Fatalf("queued state: %+v %v", started, err)
	}
	pending := awaitRelease(t, m, "awaiting_wss")
	if pending.Progress != 85 || pending.Status != "running" {
		t.Fatalf("unaccepted replacement: %+v", pending)
	}
	repeat, err := m.startRelease(ctx, opTestID, testDigest)
	if err != nil || repeat.OperationID != opTestID {
		t.Fatalf("idempotent start: %v", err)
	}
	if _, err := m.startRelease(ctx, "0192b532-184f-41bc-9952-78e15b14cc56", testDigest); err == nil {
		t.Fatal("overlapping release accepted")
	}
	if err := m.completeReleaseReady(ctx, "0192b532-184f-41bc-9952-78e15b14cc56"); err == nil {
		t.Fatal("wrong release operation accepted for WSS confirmation")
	}
	if err := m.completeReleaseReady(ctx, opTestID); err != nil {
		t.Fatal(err)
	}
	pulls := 0
	for _, call := range fake.calls {
		if len(call) > 0 && call[0] == "pull" {
			pulls++
		}
	}
	if pulls != 1 {
		t.Fatalf("a staged immutable image should be pulled exactly once, got %d", pulls)
	}
	completed := awaitRelease(t, m, "succeeded")
	if completed.Progress != 100 || completed.Phase != "completed" || m.image != testDigest {
		t.Fatalf("completion status invalid: %+v selected=%s", completed, m.image)
	}
	recovered := &manager{stateDir: m.stateDir}
	if err := recovered.loadOperation(); err != nil {
		t.Fatal(err)
	}
	if recovered.operation.Status != "succeeded" {
		t.Fatal("durable release result not loaded")
	}
	raw, err := os.ReadFile(m.operationPath())
	if err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(m.operationPath())
	if err != nil || info.Mode().Perm() != 0600 || strings.Contains(string(raw), "synthetic-private-token") {
		t.Fatal("release journal must contain no secrets and remain mode 0600")
	}
}

func TestAgentRestartsDuringReleaseRevertsSelectedImageBeforeChildReconcile(t *testing.T) {
	dir := t.TempDir()
	m := &manager{stateDir: dir, image: testDigest, operation: &nodeReleaseOperation{
		APIVersion: 1, OperationID: opTestID, Image: testDigest, PreviousImage: nodeSeed,
		Status: "running", Phase: "awaiting_wss", Progress: 85,
		StartedAt: time.Now().UTC(), UpdatedAt: time.Now().UTC(),
	}}
	if err := m.saveRelease(testDigest); err != nil {
		t.Fatal(err)
	}
	if err := m.saveOperationLocked(); err != nil {
		t.Fatal(err)
	}
	restored := &manager{stateDir: dir}
	if err := restored.loadRelease(); err != nil {
		t.Fatal(err)
	}
	if err := restored.loadOperation(); err != nil {
		t.Fatal(err)
	}
	if err := restored.reconcileInterruptedRelease(); err != nil {
		t.Fatal(err)
	}
	if restored.image != nodeSeed {
		t.Fatal("Agent restart did not select previous Node seed")
	}
	if _, err := os.Lstat(filepath.Join(dir, "node-release.json")); !os.IsNotExist(err) {
		t.Fatal("unverified selected release persisted after Agent crash")
	}
	if err := restored.setOperation("rolled_back", "interrupted", 100, "agent_restarted_during_update"); err != nil {
		t.Fatal(err)
	}
	if restored.operation.Status != "rolled_back" {
		t.Fatal("Agent interruption not recorded")
	}
}

func TestAgentFailsClosedOnTamperedReleaseJob(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "node-update.json")
	if err := os.WriteFile(path, []byte(`{"api_version":1,"operation_id":"not-uuid","image":"latest"}`), 0600); err != nil {
		t.Fatal(err)
	}
	if err := (&manager{stateDir: dir}).loadOperation(); err == nil {
		t.Fatal("untrusted job was loaded")
	}
	if err := os.Chmod(path, 0644); err != nil {
		t.Fatal(err)
	}
	if err := (&manager{stateDir: dir}).loadOperation(); err == nil {
		t.Fatal("public job was loaded")
	}
}

func TestRollbackRequiresAuthenticatedOldNodeWSS(t *testing.T) {
	fake := &fakeDocker{exists: true, owned: true, running: true, image: nodeSeed}
	m := &manager{run: fake.Run, stateDir: t.TempDir(), image: nodeSeed,
		operation: &nodeReleaseOperation{APIVersion: 1, OperationID: opTestID, Image: testDigest,
			PreviousImage: nodeSeed, Status: "running", Phase: "rollback", Progress: 92,
			StartedAt: time.Now().UTC(), UpdatedAt: time.Now().UTC()}}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan struct{})
	go func() { m.awaitRollbackReady(ctx, "wss_ready_timeout"); close(done) }()
	state := awaitRelease(t, m, "rollback_verify")
	if state.Status != "running" || state.Progress != 96 {
		t.Fatalf("old image was prematurely accepted as healthy: %+v", state)
	}
	if err := m.completeReleaseReady(ctx, opTestID); err != nil {
		t.Fatal(err)
	}
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("old Node WSS readiness was not acknowledged")
	}
	state = awaitRelease(t, m, "rolled_back")
	if state.Progress != 100 || state.ErrorCode != "wss_ready_timeout" {
		t.Fatalf("rollback state mismatch: %+v", state)
	}
}

func TestUnverifiedRollbackIsReportedFailed(t *testing.T) {
	m := &manager{stateDir: t.TempDir(), image: nodeSeed,
		operation: &nodeReleaseOperation{APIVersion: 1, OperationID: opTestID,
			Image: testDigest, PreviousImage: nodeSeed, Status: "running",
			Phase: "rollback", Progress: 92,
			StartedAt: time.Now().UTC(), UpdatedAt: time.Now().UTC()}}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { m.awaitRollbackReady(ctx, "replacement_failed"); close(done) }()
	state := awaitRelease(t, m, "rollback_verify")
	if state.Status != "running" {
		t.Fatal("old Node was called healthy without WSS")
	}
	cancel()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("rollback failure did not terminate")
	}
	state = awaitRelease(t, m, "failed")
	if state.ErrorCode != "rollback_unverified" {
		t.Fatalf("ambiguous rollback incorrectly marked as successful: %+v", state)
	}
}

func TestAgentFailedJournalWritePreservesPreviousTerminalOperation(t *testing.T) {
	dir := t.TempDir()
	fake := &fakeDocker{exists: true, owned: true, running: true, image: nodeSeed}
	prior := &nodeReleaseOperation{APIVersion: 1, OperationID: opTestID, Image: testDigest,
		PreviousImage: nodeSeed, Status: "failed", Phase: "pulling", Progress: 100,
		ErrorCode: "image_unavailable", StartedAt: time.Now().UTC(), UpdatedAt: time.Now().UTC()}
	m := &manager{run: fake.Run, stateDir: dir, image: nodeSeed, operation: prior}
	// An existing private journal filename that is a directory forces the
	// atomic replace to fail without changing the runtime or old metadata.
	if err := os.Mkdir(filepath.Join(dir, "node-update.json"), 0700); err != nil {
		t.Fatal(err)
	}
	_, err := m.startRelease(context.Background(), "0192b532-184f-41bc-9952-78e15b14cc56", testDigest)
	if err == nil {
		t.Fatal("failed journal persistence accepted an update")
	}
	if m.operation != prior || m.operation.Status != "failed" || m.ready != nil || fake.has("pull") || fake.has("stop") {
		t.Fatal("failed release journal write discarded previous operation or changed the Node")
	}
}

func TestCustomNodeReleaseRetainsTrustedSelectionAndRejectsRevocation(t *testing.T) {
	custom := "registry.example.com:5000/waylane/node@sha256:" + strings.Repeat("a", 64)
	m := &manager{stateDir: t.TempDir()}
	if err := m.trustRegistry("registry.example.com:5000/waylane/node"); err != nil {
		t.Fatal(err)
	}
	if err := m.saveRelease(custom); err != nil {
		t.Fatal(err)
	}
	restored := &manager{stateDir: m.stateDir}
	if err := restored.loadRegistryRepositories(); err != nil {
		t.Fatal(err)
	}
	if err := restored.loadRelease(); err != nil || restored.image != custom {
		t.Fatalf("trusted persisted OCI image was not restored: %v", err)
	}
	if err := (&manager{stateDir: m.stateDir}).loadRelease(); err == nil {
		t.Fatal("removed custom OCI repository left previously pinned image trusted")
	}
}

func TestAgentOneTimeAuthenticatedCustomPullNeverPersistsPassword(t *testing.T) {
	custom := "registry.example.com:5000/waylane/node@sha256:" + strings.Repeat("a", 64)
	credential := registryPullCredential{Repository: "registry.example.com:5000/waylane/node", Username: "reader", Password: "synthetic-secret-never-persist"}
	fake := &fakeDocker{exists: true, owned: true, running: true, image: nodeSeed}
	pullCount := 0
	m := &manager{run: fake.Run, stateDir: t.TempDir(), image: nodeSeed, panelURL: "https://panel.invalid"}
	m.authenticatedPull = func(_ context.Context, image string, input registryPullCredential) error {
		pullCount++
		if image != custom || input != credential {
			return errors.New("wrong private pull payload")
		}
		return nil
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	job, err := m.startRelease(ctx, "0192b532-184f-41bc-9952-78e15b14cc57", custom, credential)
	if err != nil || job.Status != "queued" {
		t.Fatalf("custom release: %+v %v", job, err)
	}
	_ = awaitRelease(t, m, "awaiting_wss")
	if err := m.completeReleaseReady(ctx, job.OperationID); err != nil {
		t.Fatal(err)
	}
	_ = awaitRelease(t, m, "succeeded")
	if pullCount != 1 || !m.approvedImage(custom) {
		t.Fatal("one-time custom pull missing")
	}
	for _, path := range []string{m.operationPath(), m.releasePath(), m.registryPath()} {
		content, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(string(content), credential.Password) || strings.Contains(string(content), credential.Username) {
			t.Fatal("credential leaked to persistent Agent journal")
		}
	}
	next := &manager{stateDir: m.stateDir}
	if err := next.loadRegistryRepositories(); err != nil {
		t.Fatal(err)
	}
	if err := next.loadRelease(); err != nil || next.image != custom {
		t.Fatalf("custom release not recoverable: %v", err)
	}
}

func TestAgentRejectsIncompleteRegistryAuthenticationBeforeCutover(t *testing.T) {
	custom := "registry.example.com:5000/waylane/node@sha256:" + strings.Repeat("a", 64)
	fake := &fakeDocker{exists: true, owned: true, running: true, image: nodeSeed}
	m := &manager{run: fake.Run, stateDir: t.TempDir(), image: nodeSeed}
	bad := registryPullCredential{Repository: "registry.example.com:5000/waylane/node", Username: "reader"}
	if _, err := m.startRelease(context.Background(), "0192b532-184f-41bc-9952-78e15b14cc57", custom, bad); err == nil {
		t.Fatal("incomplete registry credentials accepted")
	}
	if fake.has("stop") || fake.has("pull") {
		t.Fatal("failed validation touched private daemon")
	}
	if _, err := os.Stat(m.registryPath()); !os.IsNotExist(err) {
		t.Fatal("invalid registry persisted")
	}
}
