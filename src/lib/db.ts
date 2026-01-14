import { PrismaClient } from '@prisma/client'

// Construct DATABASE_URL from individual Railway PostgreSQL variables
function getDatabaseUrl(): string {
  // If DATABASE_URL is set directly, use it
  if (process.env.DATABASE_URL) {
    return process.env.DATABASE_URL
  }

  // Otherwise construct from individual variables
  const host = process.env.PGHOST
  const database = process.env.PGDATABASE
  const user = process.env.PGUSER
  const password = process.env.PGPASSWORD
  const port = process.env.PGPORT || '5432'

  if (host && database && user && password) {
    return `postgresql://${user}:${password}@${host}:${port}/${database}`
  }

  throw new Error('Database configuration missing. Set DATABASE_URL or PGHOST, PGDATABASE, PGUSER, PGPASSWORD')
}

const globalForPrisma = globalThis as unknown as {
  prisma: PrismaClient | undefined
}

export const prisma = globalForPrisma.prisma ?? new PrismaClient({
  datasources: {
    db: {
      url: getDatabaseUrl()
    }
  }
})

if (process.env.NODE_ENV !== 'production') globalForPrisma.prisma = prisma
