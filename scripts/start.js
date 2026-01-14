const { execSync } = require('child_process');

// Check for PG variables (Railway uses different naming sometimes)
const host = process.env.PGHOST || process.env.POSTGRES_HOST || process.env.DB_HOST;
const database = process.env.PGDATABASE || process.env.POSTGRES_DB || process.env.DB_NAME || 'railway';
const user = process.env.PGUSER || process.env.POSTGRES_USER || process.env.DB_USER || 'postgres';
const password = process.env.PGPASSWORD || process.env.POSTGRES_PASSWORD || process.env.DB_PASSWORD;
const port = process.env.PGPORT || process.env.POSTGRES_PORT || process.env.DB_PORT || '5432';

console.log('Database config check:');
console.log('- Host:', host || 'NOT SET');
console.log('- Database:', database || 'NOT SET');
console.log('- User:', user || 'NOT SET');
console.log('- Password:', password ? 'SET (hidden)' : 'NOT SET');
console.log('- Port:', port);

if (host && password) {
  // URL encode the password in case it has special characters
  const encodedPassword = encodeURIComponent(password);
  process.env.DATABASE_URL = `postgresql://${user}:${encodedPassword}@${host}:${port}/${database}`;
  console.log('DATABASE_URL constructed successfully');
} else if (process.env.DATABASE_URL) {
  console.log('Using existing DATABASE_URL from environment');
} else {
  console.error('ERROR: No database configuration found!');
  process.exit(1);
}

try {
  console.log('\nRunning prisma db push...');
  execSync('npx prisma db push --skip-generate', {
    stdio: 'inherit',
    env: process.env
  });
  console.log('Database synced successfully!\n');
} catch (error) {
  console.error('Failed to sync database:', error.message);
  process.exit(1);
}

console.log('Starting Next.js server...');
execSync('npm start', {
  stdio: 'inherit',
  env: process.env
});
