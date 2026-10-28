import { z } from "zod";
import { inboundPortSchema, jsonObjectSchema, jsonValueSchema, portSchema, tagSchema } from "./shared.js";
import { validateStrictXrayConfig } from "../xray-json/parity.js";

export const xrayStreamSettingsSchema = jsonObjectSchema.extend({
  network: z.string().optional(),
  security: z.string().optional()
}).passthrough();

export const xrayInboundSchema = jsonObjectSchema.extend({
  tag: tagSchema.optional(),
  listen: z.string().optional(),
  port: inboundPortSchema.optional(),
  protocol: z.string(),
  settings: jsonObjectSchema.optional(),
  streamSettings: xrayStreamSettingsSchema.optional(),
  sniffing: jsonObjectSchema.optional()
}).passthrough();

export const xrayOutboundSchema = jsonObjectSchema.extend({
  tag: tagSchema.optional(),
  protocol: z.string(),
  settings: jsonObjectSchema.optional(),
  streamSettings: xrayStreamSettingsSchema.optional()
}).passthrough();

export const xrayRoutingSchema = jsonObjectSchema.extend({
  domainStrategy: z.string().optional(),
  rules: z.array(jsonObjectSchema).optional()
}).passthrough();

export const xrayDnsSchema = jsonObjectSchema.extend({
  servers: z.array(z.union([z.string(), jsonObjectSchema])).optional(),
  hosts: z.record(z.union([z.string(), z.array(z.string()), jsonValueSchema])).optional(),
  queryStrategy: z.string().optional()
}).passthrough();

export const xrayConfigSchema = jsonObjectSchema.extend({
  log: jsonObjectSchema.optional(),
  dns: xrayDnsSchema.optional(),
  routing: xrayRoutingSchema.optional(),
  inbounds: z.array(xrayInboundSchema).optional(),
  outbounds: z.array(xrayOutboundSchema).optional(),
  policy: jsonObjectSchema.optional(),
  api: jsonObjectSchema.optional(),
  stats: jsonObjectSchema.optional(),
  metrics: jsonObjectSchema.optional(),
  fakeDns: jsonValueSchema.optional(),
  observatory: jsonObjectSchema.optional(),
  burstObservatory: jsonObjectSchema.optional(),
  geodata: jsonObjectSchema.optional(),
  version: z.union([z.string(), z.number()]).optional()
}).passthrough();

export const strictXrayConfigSchema = xrayConfigSchema.superRefine((value, context) => {
  const result = validateStrictXrayConfig(value);
  for (const issue of result.issues.filter((item) => item.severity === "error")) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      path: issue.path.split("/").filter(Boolean),
      message: issue.message
    });
  }
});

export type XrayConfigSchema = typeof xrayConfigSchema;
export type StrictXrayConfigSchema = typeof strictXrayConfigSchema;
