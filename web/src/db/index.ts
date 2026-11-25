import { Database } from "bun:sqlite";
import { drizzle } from "drizzle-orm/bun-sqlite";
import { resolveDatabaseUrl } from "./database-url.ts";
import * as schema from "./schema.ts";

const sqlite = new Database(resolveDatabaseUrl());
sqlite.exec("PRAGMA journal_mode = WAL");
sqlite.exec("PRAGMA busy_timeout = 30000");
sqlite.exec("PRAGMA foreign_keys = ON");
sqlite.exec("PRAGMA synchronous = NORMAL");

export const db = drizzle(sqlite, { schema });
