package wireguard

import (
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

// AWG31 stores AmneziaWG 3.1 obfuscation settings, separately from WG peers.
type AWG31 struct {
	Jc                     uint16 `json:"jc,omitempty"`
	Jmin                   uint16 `json:"jmin,omitempty"`
	Jmax                   uint16 `json:"jmax,omitempty"`
	S1                     uint16 `json:"s1,omitempty"`
	S2                     uint16 `json:"s2,omitempty"`
	S3                     uint16 `json:"s3,omitempty"`
	S4                     uint16 `json:"s4,omitempty"`
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
type awgRange struct{ min, max uint64 }

var cpsPattern = regexp.MustCompile(`^(?:<t>|<b 0x(?:[a-fA-F0-9]{2})+>|<(?:r|rd|rc) (?:[1-9][0-9]{0,2}|1000)>)+$`)

func parseAWGRange(name, val string, maxValue uint64) (awgRange, error) {
	if val == "" {
		return awgRange{}, nil
	}
	parts := strings.Split(val, "-")
	if len(parts) > 2 {
		return awgRange{}, fmt.Errorf("%s: expected integer or min-max", name)
	}
	a, err := strconv.ParseUint(parts[0], 10, 32)
	if err != nil || a > maxValue {
		return awgRange{}, fmt.Errorf("%s: invalid minimum", name)
	}
	b := a
	if len(parts) == 2 {
		b, err = strconv.ParseUint(parts[1], 10, 32)
		if err != nil || b > maxValue || b < a {
			return awgRange{}, fmt.Errorf("%s: invalid maximum", name)
		}
	}
	return awgRange{a, b}, nil
}
func (a *AWG31) Validate() error {
	if a == nil {
		return nil
	}
	if a.Jc > 0 && a.Jmin > a.Jmax {
		return errors.New("amnezia.jmin exceeds jmax")
	}
	vals := []struct {
		name, value string
		max         uint64
	}{
		{"h1", a.H1, 1<<32 - 1}, {"h2", a.H2, 1<<32 - 1},
		{"h3", a.H3, 1<<32 - 1}, {"h4", a.H4, 1<<32 - 1},
		{"content_padding_addition", a.ContentPaddingAddition, 65535},
		{"rekey_after_time", a.RekeyAfterTime, 65535}, {"rekey_timeout", a.RekeyTimeout, 65535},
		{"reject_after_time", a.RejectAfterTime, 65535}, {"keepalive_timeout", a.KeepaliveTimeout, 65535},
		{"max_handshake_attempts", a.MaxHandshakeAttempts, 65535},
	}
	for _, v := range vals {
		if _, err := parseAWGRange(v.name, v.value, v.max); err != nil {
			return err
		}
	}
	headers := []struct {
		name, value string
		disabled    uint64
	}{
		{"h1", a.H1, 1}, {"h2", a.H2, 2}, {"h3", a.H3, 3}, {"h4", a.H4, 4},
	}
	for i, x := range headers {
		rx, _ := parseAWGRange(x.name, x.value, 1<<32-1)
		if x.value == "" || rx.min == x.disabled && rx.max == x.disabled {
			continue
		}
		for _, y := range headers[:i] {
			ry, _ := parseAWGRange(y.name, y.value, 1<<32-1)
			if y.value != "" && !(ry.min == y.disabled && ry.max == y.disabled) && rx.min <= ry.max && ry.min <= rx.max {
				return fmt.Errorf("overlapping %s and %s", x.name, y.name)
			}
		}
	}
	if a.HeaderProtectionKey != "" {
		b, err := base64.StdEncoding.DecodeString(a.HeaderProtectionKey)
		if err != nil || len(b) != 32 {
			return errors.New("header_protection_key must be base64 for 32 bytes")
		}
		for _, p := range []uint16{a.S1, a.S2, a.S3, a.S4} {
			if p < 12 {
				return errors.New("s1-s4 must be at least 12 when header protection is enabled")
			}
		}
	}
	for _, v := range []string{a.I1, a.I2, a.I3, a.I4, a.I5} {
		if v != "" && (len(v) > 4096 || !cpsPattern.MatchString(v)) {
			return errors.New("invalid I1-I5 CPS chain")
		}
	}
	return nil
}
func (a *AWG31) UAPIFields() ([]string, error) {
	if err := a.Validate(); err != nil {
		return nil, err
	}
	if a == nil {
		return nil, nil
	}
	var out []string
	add := func(k, v string) {
		if v != "" {
			out = append(out, k+"="+v)
		}
	}
	for _, p := range []struct {
		k string
		v uint16
	}{
		{"jc", a.Jc}, {"jmin", a.Jmin}, {"jmax", a.Jmax},
		{"s1", a.S1}, {"s2", a.S2}, {"s3", a.S3}, {"s4", a.S4},
	} {
		if p.v > 0 {
			add(p.k, strconv.FormatUint(uint64(p.v), 10))
		}
	}
	for _, p := range []struct{ k, v string }{
		{"h1", a.H1}, {"h2", a.H2}, {"h3", a.H3}, {"h4", a.H4},
		{"i1", a.I1}, {"i2", a.I2}, {"i3", a.I3}, {"i4", a.I4}, {"i5", a.I5},
		{"content_padding_addition", a.ContentPaddingAddition},
		{"rekey_after_time", a.RekeyAfterTime}, {"rekey_timeout", a.RekeyTimeout},
		{"reject_after_time", a.RejectAfterTime}, {"keepalive_timeout", a.KeepaliveTimeout},
		{"max_handshake_attempts", a.MaxHandshakeAttempts},
	} {
		add(p.k, p.v)
	}
	if a.HeaderProtectionKey != "" {
		b, _ := base64.StdEncoding.DecodeString(a.HeaderProtectionKey)
		add("header_protection_key", hex.EncodeToString(b))
	}
	if a.RandomTrailers {
		add("random_trailers", "true")
	}
	if a.DisableCookies {
		add("disable_cookies", "true")
	}
	return out, nil
}
