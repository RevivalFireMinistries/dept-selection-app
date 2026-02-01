# RFM Stellenbosch Department Selection App

A mobile-friendly web app for church members to select departments they want to serve in, with admin approval workflow, publishing system, and appeals management.

## Tech Stack

- **Framework**: FastAPI (Python)
- **Database**: PostgreSQL with SQLAlchemy ORM
- **Templates**: Jinja2 with Tailwind CSS
- **Excel Export**: openpyxl library
- **Hosting**: Railway

## Project Structure

```
├── main.py                      # FastAPI app entry point with migrations
├── database.py                  # SQLAlchemy connection setup
├── models.py                    # Database models
├── schemas.py                   # Pydantic schemas for validation
├── routers/
│   ├── api.py                   # All API endpoints
│   └── pages.py                 # HTML page routes
├── templates/
│   ├── base.html                # Base template with Tailwind
│   ├── landing.html             # Home page with login/register
│   ├── index.html               # New selection form
│   ├── update.html              # Phone lookup for updates
│   ├── edit.html                # Edit existing selection
│   ├── portal.html              # Member portal (results/appeals)
│   ├── appeal.html              # Appeal submission form
│   ├── results.html             # Legacy results lookup
│   ├── admin/
│   │   ├── base.html            # Admin layout with navigation
│   │   ├── login.html           # Admin login
│   │   ├── dashboard.html       # Stats, quick actions, exports
│   │   ├── submissions.html     # View all submissions
│   │   ├── departments.html     # CRUD departments
│   │   ├── categories.html      # CRUD categories
│   │   ├── settings.html        # App settings
│   │   ├── approvals.html       # Review/approve selections
│   │   ├── publish.html         # Publish/unpublish results
│   │   ├── appeals.html         # Manage member appeals
│   │   ├── department_stats.html
│   │   └── department_detail.html
│   └── desk/
│       ├── base.html            # Info desk layout
│       ├── login.html           # Info desk login
│       ├── dashboard.html       # Search members
│       ├── new.html             # New submission for member
│       ├── member.html          # Edit member selection
│       └── profile.html         # View member profile/appeals
└── static/                      # Static assets
```

## Key Features

### Member Features
1. **Department Selection**: Select up to N departments (configurable)
2. **Category Limits**: Categories can restrict selections (e.g., pick 1 from Music Ministry)
3. **Phone Login**: Existing members login with phone number
4. **Family Support**: Multiple members can share same phone (profile selector)
5. **Member Portal**: View approved departments, pending status, rejections
6. **Appeals**: Submit appeals for approved/admin-added departments when window is open

### Admin Features
1. **Approval Workflow**: Approve/reject each department selection per member
2. **Replace/Add Departments**: Admin can replace a selection or add additional assignments
3. **Bulk Approve**: Approve all pending selections at once
4. **Publishing**: Preview and publish results (makes them visible to members)
5. **Appeal Management**: Open/close appeal window, resolve appeals
6. **Excel Exports**: Export by department or by member, with approved-only filter
7. **Department Stats**: View member counts per department

### Info Desk Features
1. **Search Members**: Find by phone or name
2. **New Submissions**: Register members who can't access the form
3. **Edit Selections**: Modify existing member choices
4. **View Profiles**: See member's approved departments
5. **Lodge Appeals**: Submit appeals on behalf of members

## Database Schema

### Models
- **Category**: Groups departments with max selection limit
- **Department**: Ministry/service area (optionally in category)
- **Member**: Person with name, phone, email, address
- **MemberDepartment**: Selection with approval status
  - `status`: "pending", "approved", "rejected"
  - `source`: "member" or "admin" (who made the selection)
  - `replaced_by_id`: Links to replacement if admin changed it
  - `admin_note`: Rejection reason or notes
- **Appeal**: Member's request to change approved departments
  - `unwanted_department_id`: Department they don't want
  - `wanted_department_id`: Department they want instead
  - `reason`: Explanation
  - `status`: "pending", "approved", "rejected"
- **Settings**: Key-value configuration store

### Key Settings
- `maxDepartments`: Maximum selections per member (default: 3)
- `adminPassword`: Admin panel password
- `deskPassword`: Info desk password
- `resultsPublished`: "true"/"false" - controls member visibility
- `appealWindowOpen`: "true"/"false" - allows appeals
- `selectionYear`: Current selection year (e.g., "2026")

## API Endpoints

### Public
- `GET /api/departments` - List all departments grouped by category
- `POST /api/members` - Submit new selection
- `GET /api/results?phone=XXX` - Get member results (all family members)
- `POST /api/results/accept/{id}?phone=XXX` - Accept admin-added department
- `POST /api/appeals` - Submit appeal

### Admin
- `GET /api/admin/reviews` - All members with selection status
- `PUT /api/admin/reviews/{id}` - Approve/reject selection
- `POST /api/admin/reviews/{id}/replace` - Replace with different department
- `POST /api/admin/members/{id}/assign` - Add department to member
- `POST /api/admin/reviews/bulk-approve` - Approve all pending
- `GET /api/admin/preview` - Preview published state
- `POST /api/admin/publish` - Publish results
- `POST /api/admin/unpublish` - Hide results
- `GET /api/admin/appeals` - List all appeals
- `PUT /api/admin/appeals/{id}` - Resolve appeal
- `POST /api/admin/appeals/window?open=true/false` - Toggle appeal window
- `GET /api/export?type=department|member&approved_only=true` - Excel export

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run development server
uvicorn main:app --reload

# Or with Python directly
python main.py
```

## Environment Variables

- `DATABASE_URL` - PostgreSQL connection string
- Or individual: `PGHOST`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGPORT`

## Railway Deployment

1. Connect GitHub repository
2. Add PostgreSQL database
3. Deploy (auto-detects Python)
4. Seed database: `curl -X POST https://your-app.railway.app/api/seed`

## Authentication

### Admin Panel
- URL: `/admin`
- Default password: `admin123`
- Cookie: `admin_session`

### Info Desk
- URL: `/desk`
- Default password: `desk123`
- Cookie: `desk_session`

### Member Portal
- URL: `/portal?phone=XXXXXXXXXX`
- Phone-based authentication (10 digits)
- Family members share phone, select profile

## User Flows

### New Member
1. Home → "New Member" → Fill form → Submit → Thank you

### Existing Member
1. Home → Enter phone → Portal
2. View approved departments (provisional if appeals open)
3. Appeal any department (especially admin-added)
4. Update selection if needed

### Admin Approval Flow
1. Members submit selections (status: pending)
2. Admin reviews in Approvals page
3. Approve, reject, or replace each selection
4. Preview final state in Publish page
5. Publish results (members can now see)
6. Open appeal window if desired
7. Resolve appeals, close window

### Info Desk Flow
1. Login at `/desk`
2. Search for member or create new
3. View profile to see approved departments
4. Lodge appeal on member's behalf if needed
