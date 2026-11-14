package main

import (
	"strings"
	"testing"
)

func TestPerNodeApprovedImageSources(t *testing.T) {
	custom := "registry.example.com:5000/waylane/node"
	customImage := custom + "@sha256:" + strings.Repeat("a", 64)
	ghcr := ghcrNodeRepository + "@sha256:" + strings.Repeat("b", 64)
	if !approvedNodeImage(ghcr) || approvedNodeImage(customImage) {
		t.Fatal("Unsafe default repository")
	}
	if !approvedNodeImageWithSources(customImage, []string{custom}) || !approvedNodeImageWithSources(ghcr, []string{custom}) {
		t.Fatal("Explicit per-node OCI registry not available")
	}
	if approvedNodeImageWithSources(customImage, []string{"other.example.com/waylane/node"}) {
		t.Fatal("Custom registry leaked to another Node")
	}
	for _, image := range []string{
		custom + ":latest", custom + "@sha256:" + strings.Repeat("A", 64),
		"evil.example.com/waylane/node@sha256:" + strings.Repeat("a", 64),
		"https://" + customImage, custom + "@sha256:" + strings.Repeat("a", 63),
	} {
		if approvedNodeImageWithSources(image, []string{custom}) {
			t.Fatalf("Unsafe image accepted: %s", image)
		}
	}
	for _, invalid := range []string{"https://registry.example.com/node", "registry.example.com/../node", "unqualified/node", "registry.example.com/node:latest"} {
		if validNodeRepository(invalid) {
			t.Fatalf("Invalid registry accepted: %s", invalid)
		}
	}
}
