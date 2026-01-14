# Church Department Selection App

A mobile-friendly Next.js web app for church members to select departments they want to serve in.

## Tech Stack

- **Framework**: Next.js 14 (App Router)
- **Styling**: Tailwind CSS 3
- **Database**: PostgreSQL with Prisma ORM
- **Excel Export**: xlsx library
- **Hosting**: Railway

## Project Structure

```
src/
├── app/
│   ├── page.tsx                 # Public member selection form
│   ├── thank-you/page.tsx       # Submission confirmation
│   ├── admin/
│   │   ├── layout.tsx           # Admin auth wrapper
│   │   ├── page.tsx             # Dashboard with stats & export
│   │   ├── submissions/         # View all member submissions
│   │   ├── departments/         # CRUD departments
│   │   ├── categories/          # CRUD categories
│   │   └── settings/            # Max depts, admin password
│   └── api/
│       ├── departments/         # GET/POST/PUT/DELETE
│       ├── categories/          # GET/POST/PUT/DELETE
│       ├── members/             # GET/DELETE submissions
│       ├── settings/            # GET/PUT settings
│       ├── submit/              # POST form submission
│       ├── seed/                # POST to initialize database
│       └── export/              # GET Excel downloads
├── components/
│   └── AdminNav.tsx             # Admin navigation bar
└── lib/
    └── db.ts                    # Prisma client singleton

prisma/
├── schema.prisma                # Database schema (PostgreSQL)
└── seed.ts                      # Local seeder script
```

## Key Features

1. **Department Categories**: Departments can be grouped into categories. Members can only select ONE department from each category.
2. **Max Selection Limit**: Configurable limit on total departments a member can select.
3. **Excel Reports**: Two export formats:
   - By Department: One sheet per department showing its members
   - By Member: One row per member with department columns

## Local Development

```bash
npm run dev      # Start development server
npm run build    # Production build
npm run start    # Start production server
```

## Database Commands

```bash
npx prisma studio              # Open database GUI
npx prisma db push             # Sync schema to database
npx prisma generate            # Regenerate Prisma client
```

## Railway Deployment

1. Add PostgreSQL database: `railway add`
2. Deploy: `railway up`
3. Get public URL: `railway domain`
4. Seed database: `curl -X POST https://your-app.railway.app/api/seed`

## Environment Variables

- `DATABASE_URL` - PostgreSQL connection string (auto-set by Railway)

## Admin Access

- URL: `/admin`
- Default password: `admin123` (change in Settings after deployment)

## Database Schema

- **Category**: Groups related departments
- **Department**: A ministry/service area (optionally linked to category)
- **Member**: Person who submitted the form
- **MemberDepartment**: Junction table for member-department selections
- **Settings**: Key-value store for app configuration
