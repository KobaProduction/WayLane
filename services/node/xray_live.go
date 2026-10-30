package main

// Xray live control runs exclusively from the Agent through its own private
// rootless Docker socket. Nothing binds the Xray gRPC API to a host-facing port.
import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sort"
	"time"
)

const xrayControlAddress = "127.0.0.1:10085"
const xrayControlTag = "waylane-agent-control"

var errLiveRequiresRestart = errors.New("Xray process requires an explicit core restart to enable this change")

func injectXrayControlAPI(raw json.RawMessage) (json.RawMessage, error) {
	var config map[string]any
	if err := json.Unmarshal(raw, &config); err != nil {
		return nil, err
	}
	if config == nil {
		return nil, errors.New("empty Xray config")
	}
	expected := map[string]any{
		"tag": xrayControlTag, "listen": xrayControlAddress,
		"services": []string{"HandlerService", "RoutingService", "StatsService"},
	}
	if existing, ok := config["api"]; ok {
		b, _ := json.Marshal(existing)
		a, _ := json.Marshal(expected)
		var existingNormalized, expectedNormalized any
		_ = json.Unmarshal(b, &existingNormalized)
		_ = json.Unmarshal(a, &expectedNormalized)
		if !reflect.DeepEqual(existingNormalized, expectedNormalized) {
			return nil, errors.New("Xray API settings are reserved for the private Agent")
		}
	}
	config["api"] = expected
	return json.Marshal(config)
}

func hasXrayControlAPI(config map[string]json.RawMessage) bool {
	var api struct {
		Tag    string `json:"tag"`
		Listen string `json:"listen"`
	}
	return json.Unmarshal(config["api"], &api) == nil &&
		api.Tag == xrayControlTag && api.Listen == xrayControlAddress
}
func xrayTags(config map[string]json.RawMessage, field string) (map[string]json.RawMessage, error) {
	var entries []json.RawMessage
	if err := json.Unmarshal(config[field], &entries); err != nil {
		return nil, fmt.Errorf("invalid Xray %s", field)
	}
	result := make(map[string]json.RawMessage, len(entries))
	for _, v := range entries {
		var obj struct {
			Tag string `json:"tag"`
		}
		if err := json.Unmarshal(v, &obj); err != nil || obj.Tag == "" || len(obj.Tag) > 128 {
			return nil, fmt.Errorf("live %s requires a unique tagged object", field)
		}
		if _, ok := result[obj.Tag]; ok {
			return nil, fmt.Errorf("duplicate live %s tag", field)
		}
		result[obj.Tag] = v
	}
	return result, nil
}
func sortedKeys(values map[string]json.RawMessage) []string {
	result := make([]string, 0, len(values))
	for tag := range values {
		result = append(result, tag)
	}
	sort.Strings(result)
	return result
}
func sameJSON(a, b json.RawMessage) bool {
	var av, bv any
	if json.Unmarshal(a, &av) != nil || json.Unmarshal(b, &bv) != nil {
		return bytes.Equal(a, b)
	}
	return reflect.DeepEqual(av, bv)
}

// Dynamic operations are scoped to routing/inbounds/outbounds; changes to API,
// stats, policy, log, metrics, DNS and other runtime modules require an explicit
// user-approved rolling/restart operation, never a hidden Xray restart.
func checkLiveFields(old, next map[string]json.RawMessage) error {
	for k, v := range next {
		if k == "routing" || k == "inbounds" || k == "outbounds" {
			continue
		}
		if !sameJSON(v, old[k]) {
			return errLiveRequiresRestart
		}
	}
	for k := range old {
		if k == "routing" || k == "inbounds" || k == "outbounds" {
			continue
		}
		if _, ok := next[k]; !ok {
			return errLiveRequiresRestart
		}
	}
	return nil
}

// usersOnlyDelta keeps all other users' listeners and active connections
// intact. Xray's HandlerService supports these four protocols; SOCKS
// deliberately falls back to replacing that one inbound handler.
func usersOnlyDelta(before, after json.RawMessage) (string, []json.RawMessage, []json.RawMessage, bool) {
	var old, newer map[string]any
	if json.Unmarshal(before, &old) != nil || json.Unmarshal(after, &newer) != nil {
		return "", nil, nil, false
	}
	oldSettings, ok1 := old["settings"].(map[string]any)
	newSettings, ok2 := newer["settings"].(map[string]any)
	protocol, _ := old["protocol"].(string)
	if !ok1 || !ok2 || protocol != newer["protocol"] {
		return "", nil, nil, false
	}
	switch protocol {
	case "vless", "vmess", "trojan", "shadowsocks":
	default:
		return "", nil, nil, false
	}
	oldClone, newClone := cloneLiveConfig(old), cloneLiveConfig(newer)
	if oldClone == nil || newClone == nil {
		return "", nil, nil, false
	}
	oldClone["settings"].(map[string]any)["clients"] = nil
	newClone["settings"].(map[string]any)["clients"] = nil
	if !reflect.DeepEqual(oldClone, newClone) {
		return "", nil, nil, false
	}
	oldList, ok1 := oldSettings["clients"].([]any)
	newList, ok2 := newSettings["clients"].([]any)
	if !ok1 || !ok2 {
		return "", nil, nil, false
	}
	indexed := func(list []any) (map[string]json.RawMessage, bool) {
		result := make(map[string]json.RawMessage)
		for _, raw := range list {
			obj, ok := raw.(map[string]any)
			if !ok {
				return nil, false
			}
			email, ok := obj["email"].(string)
			if !ok || email == "" || len(email) > 128 {
				return nil, false
			}
			if _, exists := result[email]; exists {
				return nil, false
			}
			encoded, err := json.Marshal(obj)
			if err != nil {
				return nil, false
			}
			result[email] = encoded
		}
		return result, true
	}
	older, ok1 := indexed(oldList)
	newEntries, ok2 := indexed(newList)
	if !ok1 || !ok2 {
		return "", nil, nil, false
	}
	toRemove, toAdd := []json.RawMessage{}, []json.RawMessage{}
	tags := map[string]bool{}
	for tag := range older {
		tags[tag] = true
	}
	for tag := range newEntries {
		tags[tag] = true
	}
	ordered := make([]string, 0, len(tags))
	for tag := range tags {
		ordered = append(ordered, tag)
	}
	sort.Strings(ordered)
	for _, tag := range ordered {
		a, b := older[tag], newEntries[tag]
		if sameJSON(a, b) {
			continue
		}
		if len(a) > 0 {
			toRemove = append(toRemove, a)
		}
		if len(b) > 0 {
			toAdd = append(toAdd, b)
		}
	}
	return protocol, toRemove, toAdd, true
}

func cloneLiveConfig(obj map[string]any) map[string]any {
	encoded, err := json.Marshal(obj)
	if err != nil {
		return nil
	}
	var result map[string]any
	if json.Unmarshal(encoded, &result) != nil {
		return nil
	}
	return result
}

func makeUserDeltaInbound(original, jsonUser json.RawMessage) json.RawMessage {
	var inbound map[string]any
	if json.Unmarshal(original, &inbound) != nil {
		return nil
	}
	if settings, ok := inbound["settings"].(map[string]any); ok {
		var user any
		_ = json.Unmarshal(jsonUser, &user)
		settings["clients"] = []any{user}
	}
	result, _ := json.Marshal(inbound)
	return result
}

func applyXrayLive(ctx context.Context, socket, dir string, current xrayLocalState,
	desired xrayDesired, newRuntime json.RawMessage) error {
	if current.ImageVersion != desired.ImageVersion {
		return errLiveRequiresRestart
	}
	// The last desired operation can have failed while the old Xray process
	// still serves traffic. Inspect the actual running child.
	inspectCtx, stop := context.WithTimeout(ctx, 7*time.Second)
	defer stop()
	running, inspectErr := exec.CommandContext(inspectCtx, "docker", "--host", "unix://"+socket,
		"inspect", "--format", "{{.State.Running}}", xrayChildName).Output()
	if inspectErr != nil || string(bytes.TrimSpace(running)) != "true" {
		return errLiveRequiresRestart
	}
	configFile, err := privateXrayFile(dir, "config.json")
	if err != nil {
		return err
	}
	before, err := os.ReadFile(configFile)
	if err != nil {
		return errLiveRequiresRestart
	}
	var old, next map[string]json.RawMessage
	if json.Unmarshal(before, &old) != nil || json.Unmarshal(newRuntime, &next) != nil {
		return errors.New("live Xray config malformed")
	}
	if !hasXrayControlAPI(old) || !hasXrayControlAPI(next) {
		return errLiveRequiresRestart
	}
	if err := checkLiveFields(old, next); err != nil {
		return err
	}
	oldInbound, err := xrayTags(old, "inbounds")
	if err != nil {
		return errLiveRequiresRestart
	}
	nextInbound, err := xrayTags(next, "inbounds")
	if err != nil {
		return errLiveRequiresRestart
	}
	oldOutbound, err := xrayTags(old, "outbounds")
	if err != nil {
		return errLiveRequiresRestart
	}
	nextOutbound, err := xrayTags(next, "outbounds")
	if err != nil {
		return errLiveRequiresRestart
	}
	if len(nextInbound) > 128 || len(nextOutbound) > 128 {
		return errors.New("too many Xray handler changes")
	}
	// All candidate documents live in the same read-only mount as config.json.
	// No arbitrary CLI commands/ports/paths are accepted from the panel.
	fileIndex := 0
	makeFile := func(kind string, value json.RawMessage) (string, error) {
		fileIndex++
		path, err := privateXrayFile(dir, fmt.Sprintf("live-%d-%d.json", desired.Generation, fileIndex))
		if err != nil {
			return "", err
		}
		payload, err := json.Marshal(map[string]json.RawMessage{kind: json.RawMessage("[" + string(value) + "]")})
		if err != nil {
			return "", err
		}
		if err := writePrivateFile(path, payload); err != nil {
			return "", err
		}
		return "/etc/waylane-xray/" + filepath.Base(path), nil
	}
	api := func(args ...string) error {
		call := append([]string{"exec", xrayChildName, "/usr/local/bin/xray", "api"}, args...)
		return dockerXray(ctx, socket, 12*time.Second, call...)
	}
	base := []string{"-s", xrayControlAddress}
	// Undo is local and bounded. If an API mutation fails, best-effort
	// reverse actions restore the preceding active router/handlers.
	var undos []func() error
	rollback := func(trigger error) error {
		failed := false
		for i := len(undos) - 1; i >= 0; i-- {
			if undos[i]() != nil {
				failed = true
			}
		}
		if failed {
			return fmt.Errorf("live Xray change failed and rollback needs operator inspection: %w", trigger)
		}
		return fmt.Errorf("live Xray change rolled back: %w", trigger)
	}
	// Pre-stage all files and apply handler changes deterministically.
	// This changes only the relevant listeners; the Xray process stays alive.
	for _, kind := range []string{"outbound", "inbound"} {
		older, newer := oldOutbound, nextOutbound
		remove, add := "rmo", "ado"
		if kind == "inbound" {
			older, newer = oldInbound, nextInbound
			remove, add = "rmi", "adi"
		}
		tags := map[string]bool{}
		for k := range older {
			tags[k] = true
		}
		for k := range newer {
			tags[k] = true
		}
		ordered := make([]string, 0, len(tags))
		for k := range tags {
			ordered = append(ordered, k)
		}
		sort.Strings(ordered)
		for _, tag := range ordered {
			before, after := older[tag], newer[tag]
			if sameJSON(before, after) {
				continue
			}
			if kind == "inbound" && len(before) > 0 && len(after) > 0 {
				protocol, removals, additions, usersOnly := usersOnlyDelta(before, after)
				if usersOnly && protocol != "" {
					// Revoke/rotate specific user identities without bringing
					// down the inbound listener or existing other users.
					for _, user := range removals {
						var identity struct {
							Email string `json:"email"`
						}
						_ = json.Unmarshal(user, &identity)
						if err := api("rmu", "-s", xrayControlAddress, "-tag="+tag, identity.Email); err != nil {
							return rollback(err)
						}
						// Re-adding the old account is a private file-backed op.
						file, err := makeFile("inbounds", makeUserDeltaInbound(before, user))
						if err != nil {
							return rollback(err)
						}
						undos = append(undos, func() error { return api("adu", "-s", xrayControlAddress, file) })
					}
					for _, user := range additions {
						file, err := makeFile("inbounds", makeUserDeltaInbound(after, user))
						if err != nil {
							return rollback(err)
						}
						if err := api("adu", "-s", xrayControlAddress, file); err != nil {
							return rollback(err)
						}
						var identity struct {
							Email string `json:"email"`
						}
						_ = json.Unmarshal(user, &identity)
						undos = append(undos, func() error { return api("rmu", "-s", xrayControlAddress, "-tag="+tag, identity.Email) })
					}
					continue
				}
			}
			// Current API commands accept either tag for remove or one
			// Xray config file for add. Pass the *tag*, not kind.
			if len(before) > 0 {
				// Prepare the original handler before changing active state.
				oldFile, err := makeFile(map[string]string{"inbound": "inbounds", "outbound": "outbounds"}[kind], before)
				if err != nil {
					return rollback(err)
				}
				if err := api(append(append([]string{remove}, base...), tag)...); err != nil {
					return rollback(err)
				}
				undos = append(undos, func() error { return api(append(append([]string{add}, base...), oldFile)...) })
			}
			if len(after) > 0 {
				newFile, err := makeFile(map[string]string{"inbound": "inbounds", "outbound": "outbounds"}[kind], after)
				if err != nil {
					return rollback(err)
				}
				if err := api(append(append([]string{add}, base...), newFile)...); err != nil {
					return rollback(err)
				}
				undos = append(undos, func() error { return api(append(append([]string{remove}, base...), tag)...) })
			}
		}
	}
	if !sameJSON(old["routing"], next["routing"]) {
		path, err := privateXrayFile(dir, fmt.Sprintf("live-route-%d.json", desired.Generation))
		if err != nil {
			return rollback(err)
		}
		if err := writePrivateFile(path, newRuntime); err != nil {
			return rollback(err)
		}
		if err := api(append(append([]string{"adrules"}, base...), "/etc/waylane-xray/"+filepath.Base(path))...); err != nil {
			return rollback(err)
		}
		// Original config is readable from the original file.
		undos = append(undos, func() error {
			return api(append(append([]string{"adrules"}, base...), "/etc/waylane-xray/config.json")...)
		})
	}
	if err := writePrivateFile(configFile, newRuntime); err != nil {
		return rollback(err)
	}
	return nil
}
