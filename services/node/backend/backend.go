package backend

import (
	"context"

	"github.com/KobaProduction/waylane/services/node/common"
)

type Backend interface {
	Started() bool
	Version() string
	SubscribeLogs(context.Context) <-chan string
	Restart() error
	Shutdown()
	SyncSubscription(context.Context, *common.Subscription) error
	SyncSubscriptions(context.Context, []*common.Subscription) error
	UpdateUsers(context.Context, []*common.Subscription) error
	UpdateUsersAndRestart(context.Context, []*common.Subscription) error
	GetSysStats(context.Context) (*common.BackendStatsResponse, error)
	GetStats(context.Context, *common.StatRequest) (*common.StatResponse, error)
	GetOutboundsLatency(context.Context, *common.LatencyRequest) (*common.LatencyResponse, error)
	GetSubscriptionOnlineStats(context.Context, string) (*common.OnlineStatResponse, error)
	GetSubscriptionOnlineIpListStats(context.Context, string) (*common.StatsOnlineIpListResponse, error)
}

// RoutingBackend is implemented by backends that expose xray RoutingService.
// Only *xray.Xray implements it; controller handlers type-assert it and return
// codes.Unimplemented for backends that do not (e.g. WireGuard).
type RoutingBackend interface {
	ListRoutingRules(ctx context.Context) (*common.RoutingRulesResponse, error)
	GetBalancerInfo(ctx context.Context, tag string) (*common.BalancerInfoResponse, error)
	TestRoute(ctx context.Context, req *common.TestRouteRequest) (*common.RouteResult, error)
	AddRoutingRule(ctx context.Context, ruleJSON string, shouldAppend bool) error
	RemoveRoutingRule(ctx context.Context, ruleTag string) error
	OverrideBalancerTarget(ctx context.Context, balancerTag, target string) error
}

// LiveConfigApplier is implemented by backends that can take some config
// changes without restarting the core. Only *xray.Xray implements it.
type LiveConfigApplier interface {
	// ApplyConfigLive reports false when the change needs a restart. An error
	// means a live update was tried and failed, so the core may hold a mix of
	// the old and the new config until it is restarted.
	ApplyConfigLive(ctx context.Context, config string, exclude []string) (bool, error)
}

type ConfigKey struct{}

type UsersKey struct{}
