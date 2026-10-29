package main
import (
    "encoding/json"
    "os"
    "path/filepath"
    "testing"
)
func TestXrayImageAllowlist(t *testing.T) {
    if img,err:=xrayImage("26.10.21");err!=nil || img!="ghcr.io/xtls/xray-core:26.10.21"{t.Fatal(img,err)}
    for _,bad:=range []string{"latest","xray:latest","",":1","26.10.21;sh","26.10.21@sha256:bad"} {
        if _,err:=xrayImage(bad);err==nil {t.Fatalf("accepted %q",bad)}
    }
}
func TestValidateXrayConfig(t *testing.T) {
    good:=json.RawMessage(`{"inbounds":[{"protocol":"socks","port":18443}],"outbounds":[{"protocol":"freedom"}]}`)
    if err:=validXrayConfig(good);err!=nil{t.Fatal(err)}
    for _,bad:=range []json.RawMessage{nil,json.RawMessage(`{}`),json.RawMessage(`{"inbounds":[],"outbounds":[]}`),json.RawMessage(`null`)} {
        if err:=validXrayConfig(bad);err==nil{t.Fatalf("accepted %s",bad)}
    }
}
func TestXrayLocalStateDurability(t *testing.T) {
    dir:=t.TempDir()
    a:=xrayLocalState{Generation:8,Status:"running",ImageVersion:"26.10.21",
      Config:json.RawMessage(`{"inbounds":[{}],"outbounds":[{}]}`)}
    if err:=saveXrayState(dir,a);err!=nil{t.Fatal(err)}
    b,err:=readXrayState(dir)
    if err!=nil || b.Generation!=8 || b.Status!="running" {t.Fatalf("%+v %v",b,err)}
    p:=filepath.Join(dir,"xray","state.json")
    info,err:=os.Stat(p)
    if err!=nil || info.Mode().Perm()!=0600 {t.Fatalf("private state %v %v",info,err)}
}
