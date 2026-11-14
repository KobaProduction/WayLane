package main

// Node only forwards immutable image selections from authenticated Backend WSS.
import (
	"regexp"
	"strconv"
	"strings"
)

const ghcrNodeRepository = "ghcr.io/kobaproduction/waylane-node"

var nodeImageSuffix = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
var nodeRepositoryHost = regexp.MustCompile(`^[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[1-9][0-9]{1,4})?$`)
var nodeRepositoryComponent = regexp.MustCompile(`^[a-z0-9]+(?:[._-][a-z0-9]+)*$`)

func validNodeRepository(repo string) bool {
	if repo == "" || len(repo) > 55 || strings.Contains(repo, "@") || strings.Contains(repo, "://") {
		return false
	}
	parts := strings.Split(repo, "/")
	if len(parts) < 2 || !nodeRepositoryHost.MatchString(parts[0]) || !strings.ContainsAny(parts[0], ".:") {
		return false
	}
	if strings.Contains(parts[0], ":") {
		port := strings.Split(parts[0], ":")[1]
		if n, err := strconv.Atoi(port); err != nil || n > 65535 {
			return false
		}
	}
	for _, part := range parts[1:] {
		if !nodeRepositoryComponent.MatchString(part) {
			return false
		}
	}
	return true
}
func imageRepository(image string) string {
	if len(image) > 128 {
		return ""
	}
	repo, digest, found := strings.Cut(image, "@")
	if found && validNodeRepository(repo) && nodeImageSuffix.MatchString(digest) {
		return repo
	}
	return ""
}
func approvedNodeImage(image string) bool { return imageRepository(image) == ghcrNodeRepository }
func approvedNodeImageWithSources(image string, additional []string) bool {
	repo := imageRepository(image)
	if repo == ghcrNodeRepository {
		return true
	}
	for _, allowed := range additional {
		if allowed == repo && repo != "" {
			return true
		}
	}
	return false
}

type registryPullCredential struct {
	Repository string `json:"repository"`
	Username   string `json:"username"`
	Password   string `json:"password"`
}
