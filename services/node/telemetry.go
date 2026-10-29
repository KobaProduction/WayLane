package main

import (
	"bufio"
	"os"
	"runtime"
	"strconv"
	"strings"
	"syscall"
)

type nodeRuntimeMetrics struct {
	Scope                string  `json:"scope"`
	Hostname             string  `json:"hostname"`
	OS                   string  `json:"os"`
	Arch                 string  `json:"arch"`
	CPULogicalCount      int     `json:"cpu_logical_count"`
	MemoryTotalBytes     uint64  `json:"memory_total_bytes"`
	MemoryAvailableBytes uint64  `json:"memory_available_bytes"`
	DiskTotalBytes       uint64  `json:"disk_total_bytes"`
	DiskFreeBytes        uint64  `json:"disk_free_bytes"`
	Load1                float64 `json:"load_1"`
	UptimeSeconds        float64 `json:"uptime_seconds"`
}

func parseProcMemory(source string) (uint64, uint64) {
	var total, available uint64
	scanner := bufio.NewScanner(strings.NewReader(source))
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) < 2 {
			continue
		}
		value, err := strconv.ParseUint(fields[1], 10, 64)
		if err != nil {
			continue
		}
		switch fields[0] {
		case "MemTotal:":
			total = value * 1024
		case "MemAvailable:":
			available = value * 1024
		}
	}
	return total, available
}

// Docker Desktop provides the Linux Agent's runtime and its private state
// filesystem, not physical Windows host statistics.
func collectNodeRuntimeMetrics(stateDir string) nodeRuntimeMetrics {
	name, err := nodeHostName()
	if err != nil {
		name = "unknown-host"
	}
	cpu := runtime.NumCPU()
	if cpu <= 0 {
		cpu = 1
	}
	if cpu > 4096 {
		cpu = 4096
	}
	report := nodeRuntimeMetrics{
		Scope: "agent-runtime", Hostname: name, OS: runtime.GOOS, Arch: runtime.GOARCH,
		CPULogicalCount: cpu,
	}
	if data, err := os.ReadFile("/proc/meminfo"); err == nil {
		report.MemoryTotalBytes, report.MemoryAvailableBytes = parseProcMemory(string(data))
	}
	if data, err := os.ReadFile("/proc/loadavg"); err == nil {
		fields := strings.Fields(string(data))
		if len(fields) > 0 {
			if v, e := strconv.ParseFloat(fields[0], 64); e == nil && v >= 0 && v <= 100000 {
				report.Load1 = v
			}
		}
	}
	if data, err := os.ReadFile("/proc/uptime"); err == nil {
		fields := strings.Fields(string(data))
		if len(fields) > 0 {
			if v, e := strconv.ParseFloat(fields[0], 64); e == nil && v >= 0 && v <= 1000000000 {
				report.UptimeSeconds = v
			}
		}
	}
	var stats syscall.Statfs_t
	if syscall.Statfs(stateDir, &stats) == nil {
		report.DiskTotalBytes = stats.Blocks * uint64(stats.Bsize)
		report.DiskFreeBytes = stats.Bavail * uint64(stats.Bsize)
	}
	return report
}
