package common

import "testing"

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
