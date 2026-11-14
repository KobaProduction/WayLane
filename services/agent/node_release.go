package main

// Operator-triggered Node-only release orchestration. The stable outer Agent
// never self-updates and never exposes a TCP Docker or management endpoint.
import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"time"
)

var releaseOpID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

const releaseTimeout = 3 * time.Minute
const readyTimeout = 75 * time.Second

type nodeReleaseOperation struct {
	APIVersion    int       `json:"api_version"`
	OperationID   string    `json:"operation_id"`
	Image         string    `json:"image"`
	PreviousImage string    `json:"previous_image"`
	Status        string    `json:"status"`
	Phase         string    `json:"phase"`
	Progress      int       `json:"progress"`
	ErrorCode     string    `json:"error_code,omitempty"`
	StartedAt     time.Time `json:"started_at"`
	UpdatedAt     time.Time `json:"updated_at"`
}

func (m *manager) operationPath() string { return filepath.Join(m.stateDir, "node-update.json") }

func (m *manager) loadOperation() error {
	path := m.operationPath()
	info, err := os.Lstat(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return errors.New("cannot stat private release operation")
	}
	if !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || info.Size() > 4096 {
		return errors.New("release operation file must be a private regular file")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return errors.New("cannot read release operation")
	}
	var op nodeReleaseOperation
	if json.Unmarshal(raw, &op) != nil || op.APIVersion != 1 || !releaseOpID.MatchString(op.OperationID) ||
		!approvedNodeImage(op.Image) || (op.PreviousImage != nodeSeed && !approvedNodeImage(op.PreviousImage)) ||
		op.Progress < 0 || op.Progress > 100 || op.StartedAt.IsZero() || op.UpdatedAt.IsZero() {
		return errors.New("invalid persisted Node release operation")
	}
	m.operation = &op
	return nil
}
func (m *manager) saveOperationLocked() error {
	if m.operation == nil {
		return errors.New("release operation absent")
	}
	data, err := json.Marshal(m.operation)
	if err != nil {
		return err
	}
	file, err := os.CreateTemp(m.stateDir, ".node-update-")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	if err = file.Chmod(0600); err == nil {
		_, err = file.Write(data)
	}
	if err == nil {
		err = file.Sync()
	}
	if e := file.Close(); err == nil {
		err = e
	}
	if err != nil {
		return errors.New("cannot persist release state")
	}
	if err = os.Rename(file.Name(), m.operationPath()); err != nil {
		return err
	}
	return nil
}
func (m *manager) setOperation(status, phase string, percent int, errorCode string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.operation == nil {
		return errors.New("release operation missing")
	}
	previous := *m.operation
	m.operation.Status = status
	m.operation.Phase = phase
	m.operation.Progress = percent
	m.operation.ErrorCode = errorCode
	m.operation.UpdatedAt = time.Now().UTC()
	if err := m.saveOperationLocked(); err != nil {
		*m.operation = previous
		return err
	}
	return nil
}
func (m *manager) releaseSnapshot() (nodeReleaseOperation, bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.operation == nil {
		return nodeReleaseOperation{}, false
	}
	return *m.operation, true
}
func (m *manager) startRelease(parent context.Context, id, image string) (nodeReleaseOperation, error) {
	if !releaseOpID.MatchString(id) || !approvedNodeImage(image) {
		return nodeReleaseOperation{}, errors.New("invalid immutable release request")
	}
	m.mu.Lock()
	if m.operation != nil {
		previous := *m.operation
		if previous.OperationID == id {
			m.mu.Unlock()
			if previous.Image != image {
				return nodeReleaseOperation{}, errors.New("conflicting idempotency request")
			}
			return previous, nil
		}
		if previous.Status == "queued" || previous.Status == "running" {
			m.mu.Unlock()
			return nodeReleaseOperation{}, errors.New("another Node update is in progress")
		}
	}
	now := time.Now().UTC()
	op := nodeReleaseOperation{APIVersion: 1, OperationID: id, Image: image, PreviousImage: m.image,
		Status: "queued", Phase: "queued", Progress: 0, StartedAt: now, UpdatedAt: now}
	priorOperation, priorReady, priorDone := m.operation, m.ready, m.readyDone
	m.operation = &op
	m.ready = make(chan struct{})
	m.readyDone = false
	if err := m.saveOperationLocked(); err != nil {
		m.operation, m.ready, m.readyDone = priorOperation, priorReady, priorDone
		m.mu.Unlock()
		return nodeReleaseOperation{}, err
	}
	m.mu.Unlock()
	go m.executeRelease(parent, op)
	return op, nil
}
func (m *manager) executeRelease(parent context.Context, job nodeReleaseOperation) {
	ctx, cancel := context.WithTimeout(parent, releaseTimeout)
	defer cancel()
	if err := m.setOperation("running", "pulling", 10, ""); err != nil {
		return
	}
	// No interruption to the existing Node until the immutable image is pulled.
	if err := m.stageImage(ctx, job.Image); err != nil {
		_ = m.setOperation("failed", "pulling", 100, "image_unavailable")
		return
	}
	if err := m.setOperation("running", "replacing", 55, ""); err != nil {
		return
	}
	if err := m.updateNodeStaged(ctx, job.Image); err != nil {
		rollbackCtx, stop := context.WithTimeout(context.Background(), 60*time.Second)
		defer stop()
		current, check := m.runningNodeImage(rollbackCtx)
		if check != nil || current != job.PreviousImage {
			_ = m.setOperation("failed", "rollback", 100, "rollback_failed")
			return
		}
		m.awaitRollbackReady(parent, "replacement_failed")
		return
	}
	if err := m.setOperation("running", "awaiting_wss", 85, ""); err != nil {
		// A successful Docker run with an unpersistable journal is not success.
		rollbackCtx, stop := context.WithTimeout(context.Background(), 60*time.Second)
		defer stop()
		_ = m.rollbackRelease(rollbackCtx, job.PreviousImage)
		return
	}
	if job.Image == job.PreviousImage {
		_ = m.setOperation("succeeded", "completed", 100, "")
		return
	}
	m.mu.Lock()
	ready := m.ready
	m.mu.Unlock()
	timer := time.NewTimer(readyTimeout)
	defer timer.Stop()
	select {
	case <-ready:
		_ = m.setOperation("succeeded", "completed", 100, "")
		return
	case <-timer.C:
	case <-ctx.Done():
	}
	_ = m.setOperation("running", "rollback", 92, "wss_ready_timeout")
	rollbackCtx, stop := context.WithTimeout(context.Background(), 60*time.Second)
	defer stop()
	if err := m.rollbackRelease(rollbackCtx, job.PreviousImage); err != nil {
		_ = m.setOperation("failed", "rollback", 100, "rollback_failed")
		return
	}
	// Running the previous image does not mean its authenticated WSS works.
	m.awaitRollbackReady(parent, "wss_ready_timeout")
}

// The old Node must reauthenticate to Panel WSS before rollback can be
// reported as successful. This is a bounded, operation-triggered wait.
func (m *manager) awaitRollbackReady(parent context.Context, cause string) {
	m.mu.Lock()
	m.ready = make(chan struct{})
	m.readyDone = false
	ready := m.ready
	m.mu.Unlock()
	if err := m.setOperation("running", "rollback_verify", 96, cause); err != nil {
		return
	}
	timer := time.NewTimer(readyTimeout)
	defer timer.Stop()
	select {
	case <-ready:
		_ = m.setOperation("rolled_back", "rollback", 100, cause)
	case <-timer.C:
		_ = m.setOperation("failed", "rollback", 100, "rollback_unverified")
	case <-parent.Done():
		_ = m.setOperation("failed", "rollback", 100, "rollback_unverified")
	}
}
func (m *manager) rollbackRelease(ctx context.Context, previous string) error {
	if err := m.restoreNode(ctx, previous); err != nil {
		return err
	}
	if previous == nodeSeed {
		if err := os.Remove(m.releasePath()); err != nil && !errors.Is(err, os.ErrNotExist) {
			return err
		}
	} else if err := m.saveRelease(previous); err != nil {
		return err
	}
	m.mu.Lock()
	m.image = previous
	m.mu.Unlock()
	return nil
}
func (m *manager) completeReleaseReady(ctx context.Context, id string) error {
	m.mu.Lock()
	if m.operation == nil || m.operation.OperationID != id || m.operation.Status != "running" ||
		(m.operation.Phase != "awaiting_wss" && m.operation.Phase != "rollback_verify") {
		m.mu.Unlock()
		return errors.New("no release awaiting authenticated WSS confirmation")
	}
	phase := m.operation.Phase
	image := m.operation.Image
	if phase == "rollback_verify" {
		image = m.operation.PreviousImage
	}
	ready := m.ready
	m.mu.Unlock()
	actual, err := m.runningNodeImage(ctx)
	if err != nil || actual != image {
		return errors.New("WSS-ready image does not match the selected release")
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.operation == nil || m.operation.OperationID != id || m.operation.Status != "running" ||
		m.operation.Phase != phase || m.ready != ready {
		return errors.New("release confirmation is stale")
	}
	if ready != nil && !m.readyDone {
		close(ready)
		m.readyDone = true
	}
	return nil
}
func (m *manager) reconcileInterruptedRelease() error {
	if m.operation == nil || (m.operation.Status != "queued" && m.operation.Status != "running") {
		return nil
	}
	previous := m.operation.PreviousImage
	// Do not start a different child while an old update was mid-replace.
	// Repair the persisted selected version, then ensureNode performs the
	// bounded rollback using the previous known-good local image.
	if previous == nodeSeed {
		if err := os.Remove(m.releasePath()); err != nil && !errors.Is(err, os.ErrNotExist) {
			return err
		}
	} else if err := m.saveRelease(previous); err != nil {
		return err
	}
	m.image = previous
	return m.setOperation("running", "rollback", 92, "agent_restarted_during_update")
}
func (m *manager) registerReleaseRoutes(mux *http.ServeMux, ctx context.Context) {
	mux.HandleFunc("GET /v1/node/release", func(w http.ResponseWriter, r *http.Request) {
		op, exists := m.releaseSnapshot()
		if !exists {
			op = nodeReleaseOperation{APIVersion: 1, Status: "idle", Image: m.image, Phase: "idle"}
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(op)
	})
	mux.HandleFunc("PUT /v1/node/release", func(w http.ResponseWriter, r *http.Request) {
		raw, err := io.ReadAll(io.LimitReader(r.Body, 4097))
		if err != nil || len(raw) > 4096 {
			http.Error(w, "invalid release request", 400)
			return
		}
		var cmd struct {
			APIVersion  int    `json:"api_version"`
			OperationID string `json:"operation_id"`
			Image       string `json:"image"`
		}
		dec := json.NewDecoder(bytes.NewReader(raw))
		dec.DisallowUnknownFields()
		if err := dec.Decode(&cmd); err != nil || cmd.APIVersion != 1 || !releaseOpID.MatchString(cmd.OperationID) || !approvedNodeImage(cmd.Image) {
			http.Error(w, "invalid pinned release", 400)
			return
		}
		op, err := m.startRelease(ctx, cmd.OperationID, cmd.Image)
		if err != nil {
			http.Error(w, "release conflict or private state unavailable", 409)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusAccepted)
		_ = json.NewEncoder(w).Encode(op)
	})
	mux.HandleFunc("POST /v1/node/release/ready", func(w http.ResponseWriter, r *http.Request) {
		raw, err := io.ReadAll(io.LimitReader(r.Body, 1024))
		if err != nil || len(raw) > 1023 {
			http.Error(w, "invalid readiness proof", 400)
			return
		}
		var value struct {
			OperationID string `json:"operation_id"`
			APIVersion  int    `json:"api_version"`
		}
		dec := json.NewDecoder(bytes.NewReader(raw))
		dec.DisallowUnknownFields()
		if dec.Decode(&value) != nil || value.APIVersion != 1 || !releaseOpID.MatchString(value.OperationID) {
			http.Error(w, "invalid readiness proof", 400)
			return
		}
		if err := m.completeReleaseReady(r.Context(), value.OperationID); err != nil {
			http.Error(w, "release not ready", 409)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	})
}
