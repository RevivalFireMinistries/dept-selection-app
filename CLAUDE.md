# RFM Stellenbosch Church Management System

A mobile-friendly web app for church management: department selection with approval workflows, attendance tracking, member directory, and cell group management.

## Tech Stack

- **Framework**: FastAPI (Python)
- **Database**: PostgreSQL with SQLAlchemy ORM
- **Templates**: Jinja2 with Tailwind CSS
- **Excel Export**: openpyxl library
- **QR Codes**: qrcode + Pillow libraries
- **QR Scanning**: html5-qrcode (JS, CDN)
- **Hosting**: Railway

## Project Structure

```
├── main.py                      # FastAPI app entry point with migrations
├── database.py                  # SQLAlchemy connection setup
├── models.py                    # Database models (dept selection + church mgmt)
├── schemas.py                   # Pydantic schemas for validation
├── requirements.txt             # Python dependencies
├── routers/
│   ├── api.py                   # Department selection API endpoints
│   ├── pages.py                 # HTML page routes
│   ├── attendance.py            # Attendance tracking & check-in API
│   ├── cells.py                 # Cell group management API
│   └── directory.py             # Member directory & profile API
├── templates/
│   ├── base.html                # Base template with Tailwind
│   ├── landing.html             # Home page with login/register
│   ├── index.html               # New selection form
│   ├── update.html              # Phone lookup for updates
│   ├── edit.html                # Edit existing selection
│   ├── portal.html              # Member portal (results/appeals)
│   ├── appeal.html              # Appeal submission form
│   ├── results.html             # Legacy results lookup
│   ├── checkin.html             # Public self check-in page
│   ├── my-qr.html               # Member QR code lookup/download
│   ├── admin/
│   │   ├── base.html            # Admin layout with navigation tabs
│   │   ├── login.html           # Admin login
│   │   ├── dashboard.html       # Stats, quick actions, exports
│   │   ├── submissions.html     # View all submissions
│   │   ├── departments.html     # CRUD departments
│   │   ├── categories.html      # CRUD categories
│   │   ├── settings.html        # App settings
│   │   ├── approvals.html       # Review/approve selections
│   │   ├── publish.html         # Publish/unpublish results
│   │   ├── appeals.html         # Manage member appeals
│   │   ├── visitors.html        # First-timer visitor management
│   │   ├── department_stats.html
│   │   ├── department_detail.html
│   │   └── attendance/
│   │       ├── services.html    # Manage recurring services
│   │       ├── checkin.html     # Admin mark attendance
│   │       └── reports.html     # Attendance reports & trends
│   └── desk/
│       ├── base.html            # Info desk layout
│       ├── login.html           # Info desk login
│       ├── dashboard.html       # Search members
│       ├── new.html             # New submission for member
│       ├── member.html          # Edit member selection
│       ├── profile.html         # View member profile/appeals
│       ├── checkin.html         # Desk check-in (search/QR/phone)
│       └── first-timer.html     # Register first-time visitor
├── static/
│   └── uploads/photos/          # Member photo uploads
```

## Key Features

### Department Selection (complete)
1. **Department Selection**: Select up to N departments (configurable)
2. **Category Limits**: Categories restrict selections (e.g., pick 1 from Music)
3. **Phone Login**: Existing members login with phone number
4. **Family Support**: Multiple members share same phone (profile selector)
5. **Member Portal**: View approved departments, pending status, rejections
6. **Appeals**: Submit appeals for approved/admin-added departments
7. **Approval Workflow**: Admin approve/reject/replace each selection
8. **Publishing**: Preview and publish results to members
9. **Excel Exports**: Export by department or by member

### Attendance Tracking (Phase 1-2 complete, see "Remaining Work" below)
1. **Service Management**: Define recurring services (day + time)
2. **Service Instances**: Auto-created for today's services
3. **Admin Check-in**: Search members, mark attendance
4. **Desk Check-in**: Search, QR scan, or phone lookup
5. **Public Self Check-in**: Members check in with phone at `/checkin`
6. **QR Code Check-in**: Members get QR at `/my-qr`, scan at desk
7. **QR Image Generation**: PNG QR codes via `qrcode` + `Pillow`
8. **First-Timer Registration**: Register visitors, optionally check in
9. **Visitor Management**: Track visitors, convert to members
10. **Attendance Reports**: Date range, by-service, trend charts

### Member Directory (API complete, UI pending)
1. **Enhanced Profiles**: Photo, birthday, anniversary, gender, occupation, etc.
2. **Profile Photo Upload**: Upload/delete member photos
3. **Paginated Directory**: Browse members with search
4. **Birthday/Anniversary Reports**: Filter by month
5. **Membership Stats**: Gender, marital status breakdowns

### Cell Groups (API complete, UI pending)
1. **Group Management**: Create groups with leaders and meeting details
2. **Memberships**: Add/remove members, assign roles
3. **Meeting Records**: Log meetings with topic, notes, offering
4. **Meeting Attendance**: Track who attended each meeting
5. **Cell Leader Portal**: Leaders view their groups and meetings

## Database Models

### Department Selection Models
- **Category** - Groups departments, max_selections per category
- **Department** - Ministry area (optional category)
- **Member** - Person with enhanced profile fields (see below)
- **MemberDepartment** - Selection with status/source/admin_note
- **Appeal** - Department appeal with reason/status
- **Settings** - Key-value config store

### Church Management Models (new)
- **Service** - Recurring service (name, day_of_week, start_time, is_active)
- **ServiceInstance** - Specific occurrence (service_id, date, notes, is_cancelled)
- **Attendance** - Check-in record (service_instance, member/visitor, method, time)
- **Visitor** - First-timer (name, phone, first_visit_date, converted_to_member_id)
- **MemberQRCode** - QR code for check-in (member_id, uuid code, is_active)
- **CellGroup** - Small group (name, meeting details, leader_id, assistant_id)
- **CellGroupMembership** - Member in group (role, joined_at, is_active)
- **CellMeeting** - Meeting record (date, topic, notes, offering_amount)
- **CellMeetingAttendance** - Meeting attendance (member or visitor)

### Member Enhanced Fields
- photo_url, birthday, anniversary, gender, marital_status
- occupation, emergency_contact_name, emergency_contact_phone
- member_since, is_active, updated_at

## API Endpoints

### Department Selection (routers/api.py)
- `GET /api/departments` - List departments grouped by category
- `POST /api/members` - Submit new selection
- `GET /api/results?phone=XXX` - Get member results
- `POST /api/appeals` - Submit appeal
- `GET /api/admin/reviews` - All members with status
- `PUT /api/admin/reviews/{id}` - Approve/reject
- `POST /api/admin/reviews/{id}/replace` - Replace department
- `POST /api/admin/members/{id}/assign` - Add department
- `POST /api/admin/reviews/bulk-approve` - Bulk approve
- `POST /api/admin/publish` / `POST /api/admin/unpublish`
- `GET /api/export?type=department|member&approved_only=true`

### Attendance (routers/attendance.py)
- `GET/POST /api/services` - Manage recurring services
- `GET/PUT/DELETE /api/services/{id}`
- `GET/POST /api/service-instances` - Service occurrences
- `GET /api/service-instances/today` - Today's services (auto-create)
- `GET/POST /api/service-instances/{id}/attendance` - Attendance records
- `DELETE /api/attendance/{id}` - Remove check-in
- `POST /api/checkin/phone` - Self check-in by phone
- `POST /api/checkin/qr` - QR code check-in
- `GET/POST /api/visitors` - Manage visitors
- `POST /api/visitors/{id}/convert` - Convert visitor to member
- `GET /api/members/{id}/qr` - Get/create QR code data
- `GET /api/members/{id}/qr/image` - Generate QR code PNG
- `POST /api/members/{id}/qr/regenerate` - New QR code
- `GET /api/attendance/report?start_date=&end_date=` - Reports

### Directory (routers/directory.py)
- `GET /api/directory?page=&page_size=` - Paginated member list
- `GET /api/directory/search?q=` - Search members
- `GET/PUT /api/members/{id}/profile` - Member profile
- `POST /api/members/{id}/photo` - Upload photo
- `DELETE /api/members/{id}/photo` - Remove photo
- `GET /api/reports/birthdays?month=` - Birthday report
- `GET /api/reports/anniversaries?month=` - Anniversary report
- `GET /api/reports/new-members?start_date=&end_date=`
- `GET /api/reports/membership-stats`

### Cell Groups (routers/cells.py)
- `GET/POST /api/cell-groups` - List/create groups
- `GET/PUT/DELETE /api/cell-groups/{id}`
- `GET/POST /api/cell-groups/{id}/members` - Group members
- `PUT/DELETE /api/cell-groups/{id}/members/{member_id}`
- `GET/POST /api/cell-groups/{id}/meetings` - Group meetings
- `GET/PUT/DELETE /api/cell-meetings/{id}`
- `GET/POST /api/cell-meetings/{id}/attendance`
- `DELETE /api/cell-meeting-attendance/{id}`
- `GET /api/cell-leader/my-groups?phone=` - Leader portal

## Authentication

### Admin Panel
- URL: `/admin` | Password: `admin123` (change in Settings) | Cookie: `admin_session`

### Info Desk
- URL: `/desk` | Password: `desk123` | Cookie: `desk_session`

### Member Portal
- URL: `/portal?phone=XXXXXXXXXX` | Phone-based (10 digits, family selector)

## Local Development

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

## Environment Variables

- `DATABASE_URL` - PostgreSQL connection string
- Or individual: `PGHOST`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGPORT`

## Migrations

All migrations run automatically on startup via `run_migrations()` in main.py. Uses raw SQL `ALTER TABLE` / `CREATE TABLE IF NOT EXISTS` for compatibility with existing data. New tables are also created by SQLAlchemy `create_all()`.

---

## Remaining Work (feature/church-management-system branch)

### Completed
- [x] Phase 1: Database models, schemas, API routers for attendance/cells/directory
- [x] Phase 2: Attendance tracking UI (admin, desk, public check-in pages)

### Phase 3: Visitor Management Enhancements
- [ ] Visitor follow-up tracking (contacted date, follow-up notes)
- [ ] Visitor attendance history view
- [ ] Mostly covered by `/admin/visitors` page already

### Phase 4: Member Directory UI
- [ ] Admin directory browse page (`/admin/directory`)
- [ ] Admin member profile edit page (`/admin/directory/member/{id}`)
- [ ] Photo upload UI in profile page
- [ ] Birthday/anniversary report pages
- [ ] Add "Directory" tab to admin navigation

### Phase 5: Cell Groups UI
- [ ] Admin cell groups list page (`/admin/cells`)
- [ ] Admin cell group detail page (`/admin/cells/{id}`)
- [ ] Cell meeting recording UI
- [ ] Cell meeting attendance UI
- [ ] Cell leader authentication (phone-based, verify leader_id)
- [ ] Cell leader dashboard (`/cell-leader`)
- [ ] Cell leader group management page
- [ ] Add "Cell Groups" tab to admin navigation

### Landing Page Updates
- [ ] Add check-in link to landing page (`/checkin`)
- [ ] Add QR code link to landing page (`/my-qr`)
