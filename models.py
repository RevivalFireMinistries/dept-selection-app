from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


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

    departments = relationship("MemberDepartment", back_populates="member", cascade="all, delete-orphan")
    appeals = relationship("Appeal", back_populates="member", cascade="all, delete-orphan")


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
