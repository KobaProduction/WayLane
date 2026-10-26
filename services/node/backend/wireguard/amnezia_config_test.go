package wireguard

import (
	"encoding/base64"
	"encoding/hex"
	"strings"
	"testing"
)

func TestAWG31AllDeviceOptionsUAPI(t *testing.T) {
	key := base64.StdEncoding.EncodeToString([]byte("0123456789abcdef0123456789abcdef"))
	c := &AWG31{
		Jc: 5, Jmin: 10, Jmax: 40, S1: 16, S2: 16, S3: 16, S4: 16,
		H1: "100-200", H2: "300-400", H3: "500", H4: "600",
		I1: "<b 0xfaab><r 4><t>", I2: "<rd 9>", I3: "<rc 10>", I4: "<t>", I5: "<b 0x01>",
		HeaderProtectionKey: key, ContentPaddingAddition: "1-30",
		RekeyAfterTime: "100-150", RekeyTimeout: "4-7", RejectAfterTime: "200",
		KeepaliveTimeout: "15-20", MaxHandshakeAttempts: "10",
		RandomTrailers: true, DisableCookies: true,
	}
	out, err := c.UAPIFields()
	if err != nil {
		t.Fatal(err)
	}
	data := strings.Join(out, "\n")
	names := []string{"jc", "jmin", "jmax", "s1", "s2", "s3", "s4",
		"h1", "h2", "h3", "h4", "i1", "i2", "i3", "i4", "i5", "header_protection_key",
		"content_padding_addition", "rekey_after_time", "rekey_timeout",
		"reject_after_time", "keepalive_timeout", "max_handshake_attempts",
		"random_trailers", "disable_cookies"}
	for _, name := range names {
		if !strings.Contains(data, name+"=") {
			t.Errorf("missing AWG 3.1 field %s", name)
		}
	}
	if !strings.Contains(data, "header_protection_key="+hex.EncodeToString([]byte("0123456789abcdef0123456789abcdef"))) {
		t.Fatal("incorrect header key encoding")
	}
}
func TestAWG31RejectsInvalidFeatures(t *testing.T) {
	for _, tc := range []struct {
		name     string
		settings AWG31
	}{
		{"overlap", AWG31{H1: "100-200", H2: "180-230"}},
		{"padding", AWG31{HeaderProtectionKey: base64.StdEncoding.EncodeToString(make([]byte, 32)), S1: 16, S2: 16, S3: 11, S4: 16}},
		{"key", AWG31{HeaderProtectionKey: "not-a-key"}},
		{"range", AWG31{RekeyAfterTime: "100-1"}},
		{"max range", AWG31{ContentPaddingAddition: "1-65536"}},
		{"junk", AWG31{Jc: 3, Jmin: 80, Jmax: 40}},
		{"cps injection", AWG31{I1: "<r 4>\nprivate_key=secret"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if err := tc.settings.Validate(); err == nil {
				t.Fatal("accepted unsafe AWG option")
			}
		})
	}
}
func TestWireGuardModesRemainCompatible(t *testing.T) {
	tests := []struct {
		config  string
		allowed bool
	}{
		{`{"private_key":"example","listen_port":51820}`, true},
		{`{"engine":"wireguard"}`, true},
		{`{"engine":"amneziawg","amnezia":{"random_trailers":true,"disable_cookies":true}}`, true},
		{`{"engine":"wireguard","amnezia":{"s1":5}}`, false},
		{`{"engine":"unknown"}`, false},
	}
	for _, tc := range tests {
		_, err := NewConfig(tc.config)
		if (err == nil) != tc.allowed {
			t.Fatalf("NewConfig(%s): got %v, allowed=%v", tc.config, err, tc.allowed)
		}
	}
}
