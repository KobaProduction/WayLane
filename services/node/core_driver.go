package main

import (
	"context"
	"errors"
)

// coreDriver is the internal boundary between a Node control envelope and an
// independently managed child core. It is not another network service.
// Only completed/accepted drivers belong in the runtime dispatch map.
type coreDriver interface {
	ApplyDesired(context.Context, *controlEnvelope) (coreApplyResult, error)
}

type coreApplyResult struct {
	Status    string
	ErrorCode string
}

// xrayCoreDriver preserves the original Xray generation semantics. A future
// AWG driver must own a distinct state directory and generation namespace.
type xrayCoreDriver struct {
	socket   string
	stateDir string
}

func (d xrayCoreDriver) ApplyDesired(ctx context.Context, envelope *controlEnvelope) (coreApplyResult, error) {
	desired, err := controlDesiredFromPayload(envelope.Payload)
	if err != nil {
		return coreApplyResult{}, err
	}
	if uint64(desired.Generation) != envelope.Generation {
		return coreApplyResult{}, errors.New("desired generation mismatch")
	}
	local, err := readXrayState(d.stateDir)
	if err != nil {
		return coreApplyResult{}, err
	}
	if desired.Generation > local.Generation {
		next, applyErr := applyXrayDesired(ctx, d.socket, d.stateDir, local, desired)
		if applyErr != nil {
			next.Status = "error"
			if errors.Is(applyErr, errLiveRequiresRestart) {
				next.Status = "requires_restart"
			}
			next.Error = applyErr.Error()
			if len(next.Error) > 240 {
				next.Error = next.Error[:240]
			}
		}
		if err := saveXrayState(d.stateDir, next); err != nil {
			return coreApplyResult{}, err
		}
		local = next
	}
	result := coreApplyResult{Status: local.Status}
	if local.Error != "" {
		result.ErrorCode = "xray_apply_failed"
	}
	return result, nil
}
