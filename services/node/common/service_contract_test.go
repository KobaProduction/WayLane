package common

import (
	"testing"

	"google.golang.org/protobuf/proto"
)

func TestWayLaneRPCNamespaceAndUnifiedFields(t *testing.T) {
	file := File_common_service_proto
	if got := string(file.Package()); got != "waylane.node.v1" {
		t.Fatalf("protobuf package = %q, want waylane.node.v1", got)
	}
	if got := string(file.Services().ByName("NodeService").FullName()); got != "waylane.node.v1.NodeService" {
		t.Fatalf("gRPC service name = %q", got)
	}
	if NodeService_Start_FullMethodName != "/waylane.node.v1.NodeService/Start" {
		t.Fatalf("unexpected RPC path %q", NodeService_Start_FullMethodName)
	}
	if File_common_service_proto.Messages().ByName("Wireguard").Fields().ByName("pre_shared_key") == nil {
		t.Fatal("WireGuard PSK field missing from Go RPC schema")
	}
	if File_common_service_proto.Messages().ByName("Proxy").Fields().ByName("masque") == nil {
		t.Fatal("MASQUE field missing from Go RPC schema")
	}
}

// Subscription.email is Xray's technical account-key field. Panel-managed accounts
// carry one UUIDv4 end-to-end; protobuf must round-trip the exact value.
func TestWayLaneSubscriptionUUIDThroughNodeProtobuf(t *testing.T) {
	subscriptionUUID := "92ab38da-71d4-4f8b-af64-ae9d82c356f3"
	original := &Subscription{SubscriptionUuid: subscriptionUUID, Proxies: &Proxy{}, Inbounds: []string{"entry"}}
	payload, err := proto.Marshal(original)
	if err != nil {
		t.Fatalf("marshal UUID account: %v", err)
	}
	var received Subscription
	if err := proto.Unmarshal(payload, &received); err != nil {
		t.Fatalf("unmarshal UUID account: %v", err)
	}
	if got := received.GetSubscriptionUuid(); got != subscriptionUUID {
		t.Fatalf("account UUID = %q, want %q", got, subscriptionUUID)
	}
	if len(received.GetInbounds()) != 1 || received.GetInbounds()[0] != "entry" {
		t.Fatalf("UUID account inbound lost: %v", received.GetInbounds())
	}
}
