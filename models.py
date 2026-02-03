from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, UniqueConstraint, Boolean, Date, Time, Numeric
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base
import uuid


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    max_selections = Column(Integer, nullable=False, default=1)  # Max departments selectable from this category
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    departments = relationship("Department", back_populates="category")


class Department(Base):
    __tablename__ = "departments"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    category = relationship("Category", back_populates="departments")
    member_departments = relationship("MemberDepartment", back_populates="department", cascade="all, delete-orphan")


class Member(Base):
    __tablename__ = "members"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    phone = Column(String, nullable=False)
    email = Column(String, nullable=False, default="")
    address = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Enhanced profile fields (nullable for migration compatibility)
    photo_url = Column(String, nullable=True)
    birthday = Column(Date, nullable=True)
    anniversary = Column(Date, nullable=True)
    gender = Column(String, nullable=True)  # "male", "female"
    marital_status = Column(String, nullable=True)  # "single", "married", "widowed", "divorced"
    occupation = Column(String, nullable=True)
    emergency_contact_name = Column(String, nullable=True)
    emergency_contact_phone = Column(String, nullable=True)
    member_since = Column(Date, nullable=True)
    is_active = Column(Boolean, nullable=True, server_default="true")
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    departments = relationship("MemberDepartment", back_populates="member", cascade="all, delete-orphan")
    appeals = relationship("Appeal", back_populates="member", cascade="all, delete-orphan")
    qr_code = relationship("MemberQRCode", back_populates="member", uselist=False, cascade="all, delete-orphan")
    attendance_records = relationship("Attendance", back_populates="member", cascade="all, delete-orphan")
    cell_memberships = relationship("CellGroupMembership", back_populates="member", cascade="all, delete-orphan")


class MemberDepartment(Base):
    __tablename__ = "member_departments"

    id = Column(Integer, primary_key=True, index=True)
    member_id = Column(Integer, ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    department_id = Column(Integer, ForeignKey("departments.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Approval workflow fields (nullable with server defaults for migration compatibility)
    source = Column(String, nullable=True, server_default="member")  # "member" or "admin"
    status = Column(String, nullable=True, server_default="pending")  # "pending", "approved", "rejected"
    replaced_by_id = Column(Integer, ForeignKey("member_departments.id"), nullable=True)
    admin_note = Column(String, nullable=True)
    status_changed_at = Column(DateTime(timezone=True), nullable=True)

    member = relationship("Member", back_populates="departments")
    department = relationship("Department", back_populates="member_departments")
    replaced_by = relationship("MemberDepartment", remote_side=[id], foreign_keys=[replaced_by_id])

    __table_args__ = (
        UniqueConstraint("member_id", "department_id", name="unique_member_department"),
    )


class Appeal(Base):
    __tablename__ = "appeals"

    id = Column(Integer, primary_key=True, index=True)
    member_id = Column(Integer, ForeignKey("members.id", ondelete="CASCADE"), nullable=False)

    # What they're appealing against (department they don't want)
    unwanted_department_id = Column(Integer, ForeignKey("departments.id", ondelete="CASCADE"), nullable=True)

    # What they want instead
    wanted_department_id = Column(Integer, ForeignKey("departments.id", ondelete="CASCADE"), nullable=True)

    # Member's reason for appeal
    reason = Column(String, nullable=True)

    # Status: "pending", "approved", "rejected"
    status = Column(String, nullable=False, default="pending")

    # Admin response
    admin_response = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    member = relationship("Member", back_populates="appeals")
    unwanted_department = relationship("Department", foreign_keys=[unwanted_department_id])
    wanted_department = relationship("Department", foreign_keys=[wanted_department_id])


class Settings(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, nullable=False)
    value = Column(String, nullable=False)


# ============ ATTENDANCE TRACKING MODELS ============

class Service(Base):
    """Represents a recurring service (e.g., Sunday 8am, Wednesday 6pm)"""
    __tablename__ = "services"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)  # e.g., "Sunday First Service"
    day_of_week = Column(Integer, nullable=False)  # 0=Monday, 6=Sunday
    start_time = Column(Time, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    instances = relationship("ServiceInstance", back_populates="service", cascade="all, delete-orphan")


class ServiceInstance(Base):
    """A specific occurrence of a service on a particular date"""
    __tablename__ = "service_instances"

    id = Column(Integer, primary_key=True, index=True)
    service_id = Column(Integer, ForeignKey("services.id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)
    notes = Column(String, nullable=True)
    is_cancelled = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    service = relationship("Service", back_populates="instances")
    attendance_records = relationship("Attendance", back_populates="service_instance", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("service_id", "date", name="unique_service_date"),
    )


class Visitor(Base):
    """First-time visitors who haven't become members yet"""
    __tablename__ = "visitors"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    address = Column(String, nullable=True)
    first_visit_date = Column(Date, nullable=False)
    notes = Column(String, nullable=True)
    converted_to_member_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    converted_member = relationship("Member")
    attendance_records = relationship("Attendance", back_populates="visitor", cascade="all, delete-orphan")


class Attendance(Base):
    """Records attendance for a service instance"""
    __tablename__ = "attendance"

    id = Column(Integer, primary_key=True, index=True)
    service_instance_id = Column(Integer, ForeignKey("service_instances.id", ondelete="CASCADE"), nullable=False)
    member_id = Column(Integer, ForeignKey("members.id", ondelete="CASCADE"), nullable=True)
    visitor_id = Column(Integer, ForeignKey("visitors.id", ondelete="CASCADE"), nullable=True)
    check_in_method = Column(String, nullable=False, default="admin")  # "admin", "self", "qr"
    check_in_time = Column(DateTime(timezone=True), server_default=func.now())
    checked_in_by = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True)  # Staff who checked them in

    service_instance = relationship("ServiceInstance", back_populates="attendance_records")
    member = relationship("Member", back_populates="attendance_records", foreign_keys=[member_id])
    visitor = relationship("Visitor", back_populates="attendance_records")
    checked_in_by_member = relationship("Member", foreign_keys=[checked_in_by])

    __table_args__ = (
        # A member/visitor can only check in once per service instance
        UniqueConstraint("service_instance_id", "member_id", name="unique_member_attendance"),
        UniqueConstraint("service_instance_id", "visitor_id", name="unique_visitor_attendance"),
    )


class MemberQRCode(Base):
    """QR code for quick member check-in"""
    __tablename__ = "member_qr_codes"

    id = Column(Integer, primary_key=True, index=True)
    member_id = Column(Integer, ForeignKey("members.id", ondelete="CASCADE"), nullable=False, unique=True)
    code = Column(String, nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    member = relationship("Member", back_populates="qr_code")


# ============ CELL GROUP MODELS ============

class CellGroup(Base):
    """Small group / cell / home group"""
    __tablename__ = "cell_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    meeting_day = Column(Integer, nullable=True)  # 0=Monday, 6=Sunday
    meeting_time = Column(Time, nullable=True)
    meeting_location = Column(String, nullable=True)
    leader_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    assistant_leader_id = Column(Integer, ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    leader = relationship("Member", foreign_keys=[leader_id])
    assistant_leader = relationship("Member", foreign_keys=[assistant_leader_id])
    memberships = relationship("CellGroupMembership", back_populates="cell_group", cascade="all, delete-orphan")
    meetings = relationship("CellMeeting", back_populates="cell_group", cascade="all, delete-orphan")


class CellGroupMembership(Base):
    """Membership in a cell group"""
    __tablename__ = "cell_group_memberships"

    id = Column(Integer, primary_key=True, index=True)
    cell_group_id = Column(Integer, ForeignKey("cell_groups.id", ondelete="CASCADE"), nullable=False)
    member_id = Column(Integer, ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    role = Column(String, nullable=False, default="member")  # "leader", "assistant", "member", "host"
    joined_at = Column(DateTime(timezone=True), server_default=func.now())
    left_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    cell_group = relationship("CellGroup", back_populates="memberships")
    member = relationship("Member", back_populates="cell_memberships")

    __table_args__ = (
        UniqueConstraint("cell_group_id", "member_id", name="unique_cell_membership"),
    )


class CellMeeting(Base):
    """A cell group meeting occurrence"""
    __tablename__ = "cell_meetings"

    id = Column(Integer, primary_key=True, index=True)
    cell_group_id = Column(Integer, ForeignKey("cell_groups.id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)
    topic = Column(String, nullable=True)
    notes = Column(String, nullable=True)
    offering_amount = Column(Numeric(10, 2), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    cell_group = relationship("CellGroup", back_populates="meetings")
    attendance_records = relationship("CellMeetingAttendance", back_populates="meeting", cascade="all, delete-orphan")


class CellMeetingAttendance(Base):
    """Attendance at a cell meeting"""
    __tablename__ = "cell_meeting_attendance"

    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("cell_meetings.id", ondelete="CASCADE"), nullable=False)
    member_id = Column(Integer, ForeignKey("members.id", ondelete="CASCADE"), nullable=True)
    # For visitors at cell meetings
    visitor_name = Column(String, nullable=True)
    visitor_phone = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    meeting = relationship("CellMeeting", back_populates="attendance_records")
    member = relationship("Member")
