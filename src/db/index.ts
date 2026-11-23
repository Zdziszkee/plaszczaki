import { Database } from "bun:sqlite";
import { drizzle } from "drizzle-orm/bun-sqlite";
import * as schema from "./schema.ts";

const databaseUrl = process.env.DATABASE_URL ?? "dev.db";

export const db = drizzle(new Database(databaseUrl), { schema });
