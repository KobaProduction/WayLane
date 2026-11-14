package main

import (
	"strings"
	"testing"
)

func TestOptionalNodeRepositoryUsesExactApprovedDigests(t *testing.T) {
	const custom = "registry.example.com:5000/waylane/node"
	customImage := custom + "@sha256:" + strings.Repeat("a", 64)
	ghcrImage := ghcrNodeRepository + "@sha256:" + strings.Repeat("b", 64)
	t.Setenv(extraNodeRepositoryEnv, "")
	if !approvedNodeImage(ghcrImage) || approvedNodeImage(customImage) {
		t.Fatal("default must allow only pinned official GHCR images")
	}
	t.Setenv(extraNodeRepositoryEnv, custom)
	if !approvedNodeImage(ghcrImage) || !approvedNodeImage(customImage) {
		t.Fatal("configured custom OCI source must coexist with GHCR")
	}
	for _, image := range []string{
		custom + ":latest", custom + "@sha256:" + strings.Repeat("A", 64),
		"registry.attacker.com/waylane/node@sha256:" + strings.Repeat("a", 64),
		"https://" + customImage, custom + "@sha256:" + strings.Repeat("a", 63),
		custom + "@sha256:" + strings.Repeat("a", 65),
	} {
		if approvedNodeImage(image) {
			t.Fatalf("unsafe image accepted: %s", image)
		}
	}
	t.Setenv(extraNodeRepositoryEnv, "registry.example.com/another/node")
	if approvedNodeImage(customImage) {
		t.Fatal("stale registry must be rejected after config change")
	}
}

func TestOptionalNodeRepositoryRejectsMalformedConfiguration(t *testing.T) {
	for _, value := range []string{
		"http://registry.example.com/waylane/node", "registry.example.com/waylane/node:latest",
		"registry.example.com/waylane/node@sha256:foo", "dockerhubname/waylane",
		"registry.example.com/../node", "registry.example.com//node",
		"registry.example.com/waylane/../node", "registry.example.com/" + strings.Repeat("a", 57),
	} {
		t.Setenv(extraNodeRepositoryEnv, value)
		if _, err := extraNodeRepository(); err == nil {
			t.Fatalf("accepted unsafe registry configuration: %s", value)
		}
	}
	t.Setenv(extraNodeRepositoryEnv, "")
	if _, err := extraNodeRepository(); err != nil {
		t.Fatal(err)
	}
}
