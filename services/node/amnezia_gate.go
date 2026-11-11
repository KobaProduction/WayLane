package main

import (
	"net/url"
	"os"
	"runtime"
)

// AWG is deliberately NOT advertised or dispatched by default. The pilot
// requires an explicit, loopback-only Docker Desktop DEV opt-in and an actual
// TUN device visible to the unprivileged parent Node container. This is not
// a production capability declaration or proof of successful UDP traffic.
func experimentalAWGEnabled() bool {
	if os.Getenv("AWG_EXPERIMENTAL_DEV") != "true" || os.Getenv("DEV_ALLOW_HTTP") != "true" ||
		runtime.GOOS != "linux" || runtime.GOARCH != "amd64" {
		return false
	}
	origin, err := url.Parse(os.Getenv("CONTROL_PANEL_URL"))
	if err != nil || origin.Scheme != "http" {
		return false
	}
	host := origin.Hostname()
	if host != "host.docker.internal" && host != "localhost" && host != "127.0.0.1" && host != "::1" {
		return false
	}
	device, err := os.Stat("/dev/net/tun")
	return err == nil && device.Mode()&os.ModeCharDevice != 0
}
