import { config } from 'dotenv'
import { defineConfig } from 'drizzle-kit'
import { resolveDatabaseUrl } from './src/db/database-url.ts'

config({ path: ['.env.local', '.env'] })

export default defineConfig({
  out: './drizzle',
  schema: './src/db/schema.ts',
  dialect: 'sqlite',
  dbCredentials: {
    url: resolveDatabaseUrl(),
  },
})
