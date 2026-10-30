package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"

	"google.golang.org/protobuf/types/known/structpb"
)

// awgEngine is an internal lifecycle boundary, not an additional RPC server.
type awgEngine interface {
	Start(context.Context, awgWireConfig) error
	Reconfigure(context.Context, awgWireConfig, awgWireConfig) error
	Stop(context.Context, awgWireConfig) error
	Healthy(context.Context, awgWireConfig) error
}

type awgDesired struct {
	Configured bool            `json:"configured"`
	Generation int             `json:"generation"`
	Action     string          `json:"action"`
	Config     json.RawMessage `json:"config"`
}

type awgLocalState struct {
	LastGeneration    int             `json:"last_generation"`
	AppliedGeneration int             `json:"applied_generation"`
	Status            string          `json:"status"`
	LastResultStatus  string          `json:"last_result_status"`
	Config            json.RawMessage `json:"config,omitempty"`
	ErrorCode         string          `json:"error_code,omitempty"`
}

type awgCoreDriver struct {
	engine   awgEngine
	stateDir string
}

func awgDesiredFromPayload(payload *structpb.Struct) (awgDesired, error) {
	if payload == nil {
		return awgDesired{}, errors.New("missing AWG desired payload")
	}
	raw, err := json.Marshal(normalizeControlNumbers(payload.AsMap()))
	if err != nil {
		return awgDesired{}, errors.New("invalid AWG desired payload")
	}
	var d awgDesired
	if err = json.Unmarshal(raw, &d); err != nil {
		return awgDesired{}, errors.New("invalid AWG desired body")
	}
	if !d.Configured || d.Generation < 1 || d.Generation > 1<<53 {
		return awgDesired{}, errors.New("invalid AWG generation")
	}
	switch d.Action {
	case "deploy", "reconfigure", "stop":
	default:
		return awgDesired{}, errors.New("unsupported AWG action")
	}
	return d, nil
}

func awgStateFile(dir string) (string, error) {
	if !filepath.IsAbs(dir) || filepath.Clean(dir) != dir {
		return "", errors.New("private absolute AWG state required")
	}
	path := filepath.Join(dir, "awg")
	if err := os.MkdirAll(path, 0700); err != nil {
		return "", err
	}
	info, err := os.Lstat(path)
	if err != nil || !info.IsDir() || info.Mode().Perm()&0077 != 0 {
		return "", errors.New("AWG state directory must be private")
	}
	return filepath.Join(path, "state.json"), nil
}
func readAWGState(dir string) (awgLocalState, error) {
	path, err := awgStateFile(dir)
	if err != nil {
		return awgLocalState{}, err
	}
	info, err := os.Lstat(path)
	if errors.Is(err, os.ErrNotExist) {
		return awgLocalState{}, nil
	}
	if err != nil {
		return awgLocalState{}, err
	}
	if !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || info.Size() > maxAWGConfigBytes*2 {
		return awgLocalState{}, errors.New("invalid private AWG state file")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return awgLocalState{}, err
	}
	var state awgLocalState
	if err = json.Unmarshal(data, &state); err != nil {
		return awgLocalState{}, errors.New("invalid persisted AWG state")
	}
	return state, nil
}
func saveAWGState(dir string, state awgLocalState) error {
	path, err := awgStateFile(dir)
	if err != nil {
		return err
	}
	raw, err := json.Marshal(state)
	if err != nil {
		return err
	}
	if len(raw) > maxAWGConfigBytes*2 {
		return errors.New("AWG state too large")
	}
	return writePrivateFile(path, raw)
}

// ApplyDesired is currently source-tested only. The Node does NOT register
// this driver in runControlStream until peer provisioning and rootless UDP
// are accepted. Results carry generic codes, never raw UAPI/Docker errors.
func (d awgCoreDriver) ApplyDesired(ctx context.Context, message *controlEnvelope) (coreApplyResult, error) {
	desired, err := awgDesiredFromPayload(message.Payload)
	if err != nil {
		return coreApplyResult{}, err
	}
	if message.CoreID != "awg/default" || uint64(desired.Generation) != message.Generation || desired.Action != message.Operation {
		return coreApplyResult{}, errors.New("AWG desired envelope mismatch")
	}
	state, err := readAWGState(d.stateDir)
	if err != nil {
		return coreApplyResult{}, err
	}
	if desired.Generation <= state.LastGeneration {
		if state.Status == "running" && state.LastResultStatus == "" {
			// Replayed results refer to the exact persisted interface, not any
			// other socket left in the private runtime directory.
			if len(state.Config) == 0 {
				return coreApplyResult{Status: "error", ErrorCode: "awg_unhealthy"}, nil
			}
			current, decodeErr := awgDecodeStrict(state.Config)
			if decodeErr != nil || d.engine.Healthy(ctx, current) != nil {
				return coreApplyResult{Status: "error", ErrorCode: "awg_unhealthy"}, nil
			}
		}
		result := state.LastResultStatus
		if result == "" {
			result = state.Status
		}
		return coreApplyResult{Status: result, ErrorCode: state.ErrorCode}, nil
	}
	if desired.Action != "stop" && len(desired.Config) == 0 {
		return coreApplyResult{}, errors.New("AWG config required")
	}
	var next awgWireConfig
	if desired.Action != "stop" {
		next, err = awgDecodeStrict(desired.Config)
		if err != nil {
			return coreApplyResult{}, err
		}
	}
	var old awgWireConfig
	if len(state.Config) > 0 {
		old, err = awgDecodeStrict(state.Config)
		if err != nil {
			return coreApplyResult{}, err
		}
	}
	switch desired.Action {
	case "deploy":
		if state.Status == "running" {
			return coreApplyResult{}, errors.New("AWG deploy on active child requires reconfigure or explicit stop")
		}
		err = d.engine.Start(ctx, next)
	case "reconfigure":
		if state.Status != "running" || len(state.Config) == 0 {
			return coreApplyResult{}, errors.New("AWG must be running for reconfigure")
		}
		err = d.engine.Reconfigure(ctx, old, next)
	case "stop":
		if state.Status != "running" {
			return coreApplyResult{}, errors.New("AWG is not running")
		}
		if len(state.Config) == 0 {
			return coreApplyResult{}, errors.New("AWG stored config required for safe stop")
		}
		err = d.engine.Stop(ctx, old)
	}
	state.LastGeneration = desired.Generation
	state.ErrorCode = ""
	if err != nil {
		// Failed UAPI peer updates leave the previous container running.
		// Preserve its last confirmed status and configuration for recovery.
		state.LastResultStatus = "error"
		state.ErrorCode = "awg_apply_failed"
		if errors.Is(err, errAWGRequiresRestart) {
			state.LastResultStatus = "requires_restart"
			state.ErrorCode = "awg_requires_restart"
		}
	} else {
		state.AppliedGeneration = desired.Generation
		state.LastResultStatus = ""
		if desired.Action == "stop" {
			state.Status = "stopped"
		} else {
			state.Status = "running"
			state.Config = append([]byte(nil), desired.Config...)
		}
	}
	if e := saveAWGState(d.stateDir, state); e != nil {
		return coreApplyResult{}, fmt.Errorf("AWG result state write: %w", e)
	}
	result := state.LastResultStatus
	if result == "" {
		result = state.Status
	}
	return coreApplyResult{Status: result, ErrorCode: state.ErrorCode}, nil
}
