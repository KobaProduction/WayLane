import { z } from "zod";

export const jsonValueSchema: z.ZodType<unknown> = z.lazy(() =>
  z.union([
    z.null(),
    z.boolean(),
    z.number(),
    z.string(),
    z.array(jsonValueSchema),
    z.record(jsonValueSchema)
  ])
);

export const jsonObjectSchema = z.object({}).catchall(jsonValueSchema);

export const tagSchema = z.string().min(1);

export const portSchema = z.number().int().min(1).max(65535);

export const inboundPortSchema = z.union([
  portSchema,
  z.string().min(1)
]);
