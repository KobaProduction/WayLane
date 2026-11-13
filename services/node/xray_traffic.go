package main

// Read-only, cumulative Xray per-subscription traffic from the private child
// StatsService. No counter reset, external listener, or new management channel.
import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

// Never activate recurring private StatsService reads from a source push.
// Operators must explicitly enable this non-billing read-only feature after
// the matching Backend migration and live acceptance are available.
func xrayReadOnlyTrafficEnabled() bool {
	return os.Getenv("WAYLANE_XRAY_STATS_READONLY") == "true"
}

const maxXrayStatsOutput = 240_000
const maxXrayTrafficUsers = 1024
const maxSafeTrafficCounter = uint64(9007199254740991) // Protobuf Struct numbers are float64.

var subscriptionStatsUUID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)

type xrayTrafficSample struct {
	SubscriptionUUID string `json:"subscription_uuid"`
	Uplink           uint64 `json:"uplink"`
	Downlink         uint64 `json:"downlink"`
}

type limitedStatsBuffer struct{ buffer bytes.Buffer }

func (b *limitedStatsBuffer) Write(p []byte) (int, error) {
	if len(p)+b.buffer.Len() > maxXrayStatsOutput {
		return 0, errors.New("Xray stats exceeds bounded output")
	}
	return b.buffer.Write(p)
}

func decodeStatsCounter(raw json.RawMessage) (uint64, error) {
	var value string
	if len(raw) > 0 && raw[0] == '"' {
		if err := json.Unmarshal(raw, &value); err != nil {
			return 0, err
		}
	} else {
		value = string(raw)
	}
	result, err := strconv.ParseUint(value, 10, 64)
	if err != nil || result > maxSafeTrafficCounter {
		return 0, errors.New("invalid Xray traffic counter")
	}
	return result, nil
}

func parseXrayTrafficStats(raw []byte) ([]xrayTrafficSample, error) {
	var document struct {
		Stat []struct {
			Name  string          `json:"name"`
			Value json.RawMessage `json:"value"`
		} `json:"stat"`
	}
	if len(raw) > maxXrayStatsOutput || json.Unmarshal(raw, &document) != nil {
		return nil, errors.New("invalid Xray stats JSON")
	}
	if len(document.Stat) > maxXrayTrafficUsers*2+128 {
		return nil, errors.New("too many Xray stat counters")
	}
	byUUID := map[string]*xrayTrafficSample{}
	seen := map[string]bool{}
	for _, item := range document.Stat {
		fields := strings.Split(item.Name, ">>>")
		if len(fields) != 4 || fields[0] != "user" || fields[2] != "traffic" {
			continue
		}
		uid := fields[1]
		if !subscriptionStatsUUID.MatchString(uid) || (fields[3] != "uplink" && fields[3] != "downlink") {
			continue
		}
		if seen[item.Name] {
			return nil, errors.New("duplicate Xray traffic counter")
		}
		seen[item.Name] = true
		counter, err := decodeStatsCounter(item.Value)
		if err != nil {
			return nil, err
		}
		sample, ok := byUUID[uid]
		if !ok {
			if len(byUUID) >= maxXrayTrafficUsers {
				return nil, errors.New("too many Xray subscription traffic records")
			}
			sample = &xrayTrafficSample{SubscriptionUUID: uid}
			byUUID[uid] = sample
		}
		if fields[3] == "uplink" {
			sample.Uplink = counter
		} else {
			sample.Downlink = counter
		}
	}
	result := make([]xrayTrafficSample, 0, len(byUUID))
	for _, sample := range byUUID {
		result = append(result, *sample)
	}
	// Predictable ordering simplifies cross-language regressions and WSS diffs.
	sort.Slice(result, func(i, j int) bool {
		return result[i].SubscriptionUUID < result[j].SubscriptionUUID
	})
	return result, nil
}

func readXrayTraffic(ctx context.Context, socket string) (map[string]any, error) {
	if socket != "/run/user/1000/docker.sock" {
		return nil, errors.New("unexpected private Docker socket")
	}
	checkCtx, stop := context.WithTimeout(ctx, 3*time.Second)
	defer stop()
	inspect := exec.CommandContext(checkCtx, "docker", "--host", "unix://"+socket, "inspect", "--format", "{{.State.StartedAt}}", xrayChildName)
	epochRaw, err := inspect.Output()
	if err != nil {
		return nil, err
	}
	epoch := strings.TrimSpace(string(epochRaw))
	parsed, err := time.Parse(time.RFC3339Nano, epoch)
	if err != nil || parsed.IsZero() || parsed.After(time.Now().Add(time.Minute)) {
		return nil, errors.New("invalid Xray start epoch")
	}
	queryCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	cmd := exec.CommandContext(queryCtx, "docker", "--host", "unix://"+socket, "exec", xrayChildName,
		"/usr/local/bin/xray", "api", "statsquery", "-s", xrayControlAddress, "-pattern", "user>>>", "-reset=false")
	var output limitedStatsBuffer
	cmd.Stdout = &output
	if err = cmd.Run(); err != nil {
		return nil, fmt.Errorf("private Xray statistics query unavailable: %w", err)
	}
	samples, err := parseXrayTrafficStats(output.buffer.Bytes())
	if err != nil {
		return nil, err
	}
	if len(samples) == 0 {
		return nil, nil
	}
	records := make([]any, 0, len(samples))
	for _, sample := range samples {
		records = append(records, map[string]any{
			"subscription_uuid": sample.SubscriptionUUID,
			"uplink":            float64(sample.Uplink), "downlink": float64(sample.Downlink),
		})
	}
	return map[string]any{"epoch": epoch, "samples": records}, nil
}
