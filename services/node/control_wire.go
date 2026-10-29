package main

// A single protobuf wire envelope is exchanged over binary WebSocket frames.
// This hand-written wire format implements the checked-in
// control.proto schema without a protoc binary in the build toolchain.
import (
	"errors"
	"fmt"

	"google.golang.org/protobuf/encoding/protowire"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/structpb"
)

const controlProtocolVersion uint32 = 1
const maxControlFrameBytes = 2_000_000

type controlEnvelope struct {
	ProtocolVersion uint32
	NodeUUID        string
	MessageID       string
	Kind            string
	CoreID          string
	Generation      uint64
	Operation       string
	Payload         *structpb.Struct
	Status          string
	ErrorCode       string
}

func appendControlString(dst []byte, number protowire.Number, value string) []byte {
	if value == "" {
		return dst
	}
	dst = protowire.AppendTag(dst, number, protowire.BytesType)
	return protowire.AppendString(dst, value)
}
func (m *controlEnvelope) MarshalBinary() ([]byte, error) {
	if m.ProtocolVersion != controlProtocolVersion || m.NodeUUID == "" || m.MessageID == "" || m.Kind == "" {
		return nil, errors.New("invalid control envelope")
	}
	var out []byte
	out = protowire.AppendTag(out, 1, protowire.VarintType)
	out = protowire.AppendVarint(out, uint64(m.ProtocolVersion))
	out = appendControlString(out, 2, m.NodeUUID)
	out = appendControlString(out, 3, m.MessageID)
	out = appendControlString(out, 4, m.Kind)
	out = appendControlString(out, 5, m.CoreID)
	if m.Generation != 0 {
		out = protowire.AppendTag(out, 6, protowire.VarintType)
		out = protowire.AppendVarint(out, m.Generation)
	}
	out = appendControlString(out, 7, m.Operation)
	if m.Payload != nil {
		payload, err := proto.Marshal(m.Payload)
		if err != nil {
			return nil, err
		}
		out = protowire.AppendTag(out, 8, protowire.BytesType)
		out = protowire.AppendBytes(out, payload)
	}
	out = appendControlString(out, 9, m.Status)
	out = appendControlString(out, 10, m.ErrorCode)
	if len(out) > maxControlFrameBytes {
		return nil, errors.New("control frame exceeds 2 MiB")
	}
	return out, nil
}

func (m *controlEnvelope) UnmarshalBinary(raw []byte) error {
	if len(raw) == 0 || len(raw) > maxControlFrameBytes {
		return errors.New("invalid binary control size")
	}
	*m = controlEnvelope{}
	for len(raw) > 0 {
		field, kind, n := protowire.ConsumeTag(raw)
		if n < 0 {
			return protowire.ParseError(n)
		}
		raw = raw[n:]
		switch field {
		case 1, 6:
			if kind != protowire.VarintType {
				return errors.New("invalid control integer")
			}
			number, size := protowire.ConsumeVarint(raw)
			if size < 0 {
				return protowire.ParseError(size)
			}
			raw = raw[size:]
			if field == 1 {
				m.ProtocolVersion = uint32(number)
			} else {
				m.Generation = number
			}
		case 2, 3, 4, 5, 7, 8, 9, 10:
			if kind != protowire.BytesType {
				return errors.New("invalid control bytes field")
			}
			data, size := protowire.ConsumeBytes(raw)
			if size < 0 {
				return protowire.ParseError(size)
			}
			raw = raw[size:]
			switch field {
			case 2:
				m.NodeUUID = string(data)
			case 3:
				m.MessageID = string(data)
			case 4:
				m.Kind = string(data)
			case 5:
				m.CoreID = string(data)
			case 7:
				m.Operation = string(data)
			case 8:
				m.Payload = &structpb.Struct{}
				if err := proto.Unmarshal(data, m.Payload); err != nil {
					return err
				}
			case 9:
				m.Status = string(data)
			case 10:
				m.ErrorCode = string(data)
			}
		default:
			size := protowire.ConsumeFieldValue(field, kind, raw)
			if size < 0 {
				return protowire.ParseError(size)
			}
			raw = raw[size:]
		}
	}
	if m.ProtocolVersion != controlProtocolVersion || m.NodeUUID == "" || m.MessageID == "" || m.Kind == "" ||
		len(m.MessageID) > 64 || len(m.CoreID) > 128 {
		return fmt.Errorf("unsupported or invalid protobuf control envelope")
	}
	return nil
}
