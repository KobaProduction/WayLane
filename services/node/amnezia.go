package main

// AmneziaWG 3.1 UAPI preparation is deliberately independent of Xray and of
// the Docker launcher. This codec validates the already existing WayLane
// WireGuard core JSON. No AWG driver is registered with the control session
// until the inner-container TUN/UDP lifecycle and authenticated UAPI work.

import (
	"bytes"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/netip"
	"regexp"
	"strconv"
	"strings"
)

const maxAWGConfigBytes = 256 * 1024

var awgInterfaceName = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,14}$`)
var awgCPS = regexp.MustCompile(`^(?:<t>|<b 0x(?:[a-fA-F0-9]{2})+>|<(?:r|rd|rc) (?:[1-9][0-9]{0,2}|1000)>)+$`)

type awgWireConfig struct {
	Engine        string       `json:"engine"`
	InterfaceName string       `json:"interface_name"`
	PrivateKey    string       `json:"private_key"`
	ListenPort    int          `json:"listen_port"`
	Address       []string     `json:"address"`
	Amnezia       awg31Options `json:"amnezia"`
	Peers         []awgPeer    `json:"peers,omitempty"`
	// Derived by the backend for client exports; never write it to UAPI.
	PublicKey    string `json:"public_key,omitempty"`
	MTU          int    `json:"mtu,omitempty"`
	PreSharedKey string `json:"pre_shared_key,omitempty"`
}

type awgPeer struct {
	PublicKey    string   `json:"public_key"`
	PreSharedKey string   `json:"pre_shared_key,omitempty"`
	AllowedIPs   []string `json:"allowed_ips"`
	Keepalive    int      `json:"persistent_keepalive_interval,omitempty"`
}

type awg31Options struct {
	Jc                     int    `json:"jc,omitempty"`
	Jmin                   int    `json:"jmin,omitempty"`
	Jmax                   int    `json:"jmax,omitempty"`
	S1                     int    `json:"s1,omitempty"`
	S2                     int    `json:"s2,omitempty"`
	S3                     int    `json:"s3,omitempty"`
	S4                     int    `json:"s4,omitempty"`
	H1                     string `json:"h1,omitempty"`
	H2                     string `json:"h2,omitempty"`
	H3                     string `json:"h3,omitempty"`
	H4                     string `json:"h4,omitempty"`
	I1                     string `json:"i1,omitempty"`
	I2                     string `json:"i2,omitempty"`
	I3                     string `json:"i3,omitempty"`
	I4                     string `json:"i4,omitempty"`
	I5                     string `json:"i5,omitempty"`
	HeaderProtectionKey    string `json:"header_protection_key,omitempty"`
	ContentPaddingAddition string `json:"content_padding_addition,omitempty"`
	RekeyAfterTime         string `json:"rekey_after_time,omitempty"`
	RekeyTimeout           string `json:"rekey_timeout,omitempty"`
	RejectAfterTime        string `json:"reject_after_time,omitempty"`
	KeepaliveTimeout       string `json:"keepalive_timeout,omitempty"`
	MaxHandshakeAttempts   string `json:"max_handshake_attempts,omitempty"`
	RandomTrailers         bool   `json:"random_trailers,omitempty"`
	DisableCookies         bool   `json:"disable_cookies,omitempty"`
}

func awgDecodeStrict(raw []byte) (awgWireConfig, error) {
	if len(raw) == 0 || len(raw) > maxAWGConfigBytes {
		return awgWireConfig{}, errors.New("invalid AWG config size")
	}
	var c awgWireConfig
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&c); err != nil {
		return awgWireConfig{}, errors.New("invalid AWG configuration fields")
	}
	var more any
	if err := dec.Decode(&more); err == nil {
		return awgWireConfig{}, errors.New("additional AWG JSON document")
	} else if !errors.Is(err, io.EOF) {
		return awgWireConfig{}, errors.New("invalid trailing AWG configuration")
	}
	if c.Engine != "amneziawg" && c.Engine != "wireguard" {
		return awgWireConfig{}, errors.New("AWG Core requires an explicit AWG or plain WireGuard profile")
	}
	if c.Engine == "wireguard" && c.Amnezia != (awg31Options{}) {
		return awgWireConfig{}, errors.New("plain WireGuard may not contain obfuscation settings")
	}
	if !awgInterfaceName.MatchString(c.InterfaceName) {
		return awgWireConfig{}, errors.New("invalid AWG interface name")
	}
	if c.ListenPort < 1 || c.ListenPort > 65535 {
		return awgWireConfig{}, errors.New("invalid AWG listen port")
	}
	if c.MTU != 0 && (c.MTU < 576 || c.MTU > 9000) {
		return awgWireConfig{}, errors.New("invalid AWG MTU")
	}
	if len(c.Address) == 0 || len(c.Address) > 16 {
		return awgWireConfig{}, errors.New("invalid AWG address count")
	}
	for _, a := range c.Address {
		if _, err := netip.ParsePrefix(a); err != nil {
			return awgWireConfig{}, errors.New("invalid AWG interface address")
		}
	}
	if _, err := awgKeyHex(c.PrivateKey); err != nil {
		return awgWireConfig{}, errors.New("invalid AWG private key")
	}
	// The older WireGuard Core schema can carry a global pre_shared_key,
	// but AWG UAPI only accepts preshared keys per peer. Reject rather than
	// silently ignoring a credential or assigning it to the wrong peer.
	if c.PreSharedKey != "" {
		return awgWireConfig{}, errors.New("global AWG preshared key requires per-peer mapping")
	}
	if c.PublicKey != "" {
		if _, err := awgKeyHex(c.PublicKey); err != nil {
			return awgWireConfig{}, errors.New("invalid AWG public key")
		}
	}
	if err := c.Amnezia.validate(); err != nil {
		return awgWireConfig{}, err
	}
	if len(c.Peers) > 2048 {
		return awgWireConfig{}, errors.New("too many AWG peers")
	}
	seen := make(map[string]struct{}, len(c.Peers))
	for _, peer := range c.Peers {
		if _, err := awgKeyHex(peer.PublicKey); err != nil {
			return awgWireConfig{}, errors.New("invalid AWG peer public key")
		}
		if _, ok := seen[peer.PublicKey]; ok {
			return awgWireConfig{}, errors.New("duplicate AWG peer key")
		}
		seen[peer.PublicKey] = struct{}{}
		if peer.PreSharedKey != "" {
			if _, err := awgKeyHex(peer.PreSharedKey); err != nil {
				return awgWireConfig{}, errors.New("invalid AWG peer preshared key")
			}
		}
		if len(peer.AllowedIPs) == 0 || len(peer.AllowedIPs) > 64 {
			return awgWireConfig{}, errors.New("invalid AWG peer address count")
		}
		for _, ip := range peer.AllowedIPs {
			if _, err := netip.ParsePrefix(ip); err != nil {
				return awgWireConfig{}, errors.New("invalid AWG peer CIDR")
			}
		}
		if peer.Keepalive < 0 || peer.Keepalive > 65535 {
			return awgWireConfig{}, errors.New("invalid AWG peer keepalive")
		}
	}
	return c, nil
}

func awgKeyHex(value string) (string, error) {
	// Reject non-canonical base64 and whitespace rather than sanitizing keys.
	raw, err := base64.StdEncoding.Strict().DecodeString(value)
	if err != nil || len(raw) != 32 || base64.StdEncoding.EncodeToString(raw) != value {
		return "", errors.New("invalid 32-byte key")
	}
	return hex.EncodeToString(raw), nil
}

func awgRange(value string, max uint64) (uint64, uint64, error) {
	if value == "" {
		return 0, 0, nil
	}
	parts := strings.Split(value, "-")
	if len(parts) > 2 {
		return 0, 0, errors.New("invalid range")
	}
	first, err := strconv.ParseUint(parts[0], 10, 64)
	if err != nil || first > max {
		return 0, 0, errors.New("invalid range minimum")
	}
	last := first
	if len(parts) == 2 {
		last, err = strconv.ParseUint(parts[1], 10, 64)
		if err != nil || last > max || last < first {
			return 0, 0, errors.New("invalid range maximum")
		}
	}
	return first, last, nil
}

func (a awg31Options) validate() error {
	for _, n := range []int{a.Jc, a.Jmin, a.Jmax, a.S1, a.S2, a.S3, a.S4} {
		if n < 0 || n > 65535 {
			return errors.New("AWG 3.1 numeric setting outside range")
		}
	}
	if a.Jc > 0 && a.Jmin > a.Jmax {
		return errors.New("AWG jmin exceeds jmax")
	}
	headers := []string{a.H1, a.H2, a.H3, a.H4}
	for i, v := range headers {
		lo, hi, err := awgRange(v, 1<<32-1)
		if err != nil {
			return errors.New("invalid AWG header")
		}
		if v == "" || (lo == uint64(i+1) && hi == lo) {
			continue
		}
		for j, prev := range headers[:i] {
			low, high, e := awgRange(prev, 1<<32-1)
			if e != nil {
				return errors.New("invalid AWG header")
			}
			if prev != "" && !(low == uint64(j+1) && high == low) && lo <= high && low <= hi {
				return errors.New("overlapping AWG packet headers")
			}
		}
	}
	for _, v := range []string{a.ContentPaddingAddition, a.RekeyAfterTime, a.RekeyTimeout, a.RejectAfterTime, a.KeepaliveTimeout, a.MaxHandshakeAttempts} {
		if _, _, err := awgRange(v, 65535); err != nil {
			return errors.New("invalid AWG range setting")
		}
	}
	for _, v := range []string{a.I1, a.I2, a.I3, a.I4, a.I5} {
		if v != "" && (len(v) > 4096 || !awgCPS.MatchString(v)) {
			return errors.New("invalid AWG CPS setting")
		}
	}
	if a.HeaderProtectionKey != "" {
		if _, err := awgKeyHex(a.HeaderProtectionKey); err != nil {
			return errors.New("invalid AWG header protection key")
		}
		for _, v := range []int{a.S1, a.S2, a.S3, a.S4} {
			if v < 12 {
				return errors.New("AWG S1-S4 must be >=12 with header protection")
			}
		}
	}
	return nil
}

// awgUAPISet builds the private Unix socket request for the child daemon.
// Callers must never log the returned bytes or pass them as process arguments.
// Applying the request and adjusting TUN addresses/UDP routes are separate
// unaccepted runtime steps. The complete peer set is replaced atomically.
func awgUAPISet(config awgWireConfig) ([]byte, error) {
	// Validate even when invoked without the JSON parser.
	raw, err := json.Marshal(config)
	if err != nil {
		return nil, err
	}
	if _, err = awgDecodeStrict(raw); err != nil {
		return nil, err
	}
	var b strings.Builder
	b.WriteString("set=1\n")
	add := func(k, v string) {
		if v != "" {
			b.WriteString(k)
			b.WriteByte('=')
			b.WriteString(v)
			b.WriteByte('\n')
		}
	}
	for _, x := range []struct {
		k string
		n int
	}{{"jc", config.Amnezia.Jc}, {"jmin", config.Amnezia.Jmin}, {"jmax", config.Amnezia.Jmax}, {"s1", config.Amnezia.S1}, {"s2", config.Amnezia.S2}, {"s3", config.Amnezia.S3}, {"s4", config.Amnezia.S4}} {
		if x.n > 0 {
			add(x.k, strconv.Itoa(x.n))
		}
	}
	for _, x := range []struct{ k, v string }{{"h1", config.Amnezia.H1}, {"h2", config.Amnezia.H2}, {"h3", config.Amnezia.H3}, {"h4", config.Amnezia.H4}, {"i1", config.Amnezia.I1}, {"i2", config.Amnezia.I2}, {"i3", config.Amnezia.I3}, {"i4", config.Amnezia.I4}, {"i5", config.Amnezia.I5}, {"content_padding_addition", config.Amnezia.ContentPaddingAddition}, {"rekey_after_time", config.Amnezia.RekeyAfterTime}, {"rekey_timeout", config.Amnezia.RekeyTimeout}, {"reject_after_time", config.Amnezia.RejectAfterTime}, {"keepalive_timeout", config.Amnezia.KeepaliveTimeout}, {"max_handshake_attempts", config.Amnezia.MaxHandshakeAttempts}} {
		add(x.k, x.v)
	}
	if config.Amnezia.HeaderProtectionKey != "" {
		key, _ := awgKeyHex(config.Amnezia.HeaderProtectionKey)
		add("header_protection_key", key)
	}
	if config.Amnezia.RandomTrailers {
		add("random_trailers", "true")
	}
	if config.Amnezia.DisableCookies {
		add("disable_cookies", "true")
	}
	key, _ := awgKeyHex(config.PrivateKey)
	add("private_key", key)
	add("listen_port", strconv.Itoa(config.ListenPort))
	b.WriteString("replace_peers=true\n")
	for _, peer := range config.Peers {
		key, _ = awgKeyHex(peer.PublicKey)
		add("public_key", key)
		if peer.PreSharedKey != "" {
			preshared, _ := awgKeyHex(peer.PreSharedKey)
			add("preshared_key", preshared)
		}
		b.WriteString("replace_allowed_ips=true\n")
		for _, cidr := range peer.AllowedIPs {
			add("allowed_ip", cidr)
		}
		if peer.Keepalive > 0 {
			add("persistent_keepalive_interval", strconv.Itoa(peer.Keepalive))
		}
	}
	b.WriteByte('\n')
	if b.Len() > maxAWGConfigBytes {
		return nil, fmt.Errorf("AWG UAPI request exceeds limit")
	}
	return []byte(b.String()), nil
}
