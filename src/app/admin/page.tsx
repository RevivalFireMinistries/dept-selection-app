'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'

interface Stats {
  totalMembers: number
  totalDepartments: number
  totalCategories: number
}

export default function AdminDashboard() {
  const [stats, setStats] = useState<Stats>({ totalMembers: 0, totalDepartments: 0, totalCategories: 0 })
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchStats()
  }, [])

  const fetchStats = async () => {
    try {
      const [membersRes, deptsRes, catsRes] = await Promise.all([
        fetch('/api/members'),
        fetch('/api/departments'),
        fetch('/api/categories')
      ])

      const members = await membersRes.json()
      const depts = await deptsRes.json()
      const cats = await catsRes.json()

      setStats({
        totalMembers: Array.isArray(members) ? members.length : 0,
        totalDepartments: (depts.categories?.reduce((acc: number, c: any) => acc + c.departments.length, 0) || 0) + (depts.uncategorized?.length || 0),
        totalCategories: Array.isArray(cats) ? cats.length : 0
      })
    } catch (error) {
      console.error('Error fetching stats:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleExport = async (type: 'department' | 'member') => {
    window.open(`/api/export?type=${type}`, '_blank')
  }

  if (loading) {
    return <div className="text-center py-8">Loading...</div>
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>

      {/* Stats */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white rounded-lg shadow p-6">
          <div className="text-3xl font-bold text-blue-600">{stats.totalMembers}</div>
          <div className="text-gray-600">Total Submissions</div>
        </div>
        <div className="bg-white rounded-lg shadow p-6">
          <div className="text-3xl font-bold text-green-600">{stats.totalDepartments}</div>
          <div className="text-gray-600">Departments</div>
        </div>
        <div className="bg-white rounded-lg shadow p-6">
          <div className="text-3xl font-bold text-purple-600">{stats.totalCategories}</div>
          <div className="text-gray-600">Categories</div>
        </div>
      </div>

      {/* Export Reports */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Export Reports</h2>
        <div className="flex flex-wrap gap-4">
          <button
            onClick={() => handleExport('department')}
            className="bg-green-600 text-white py-2 px-4 rounded-lg font-medium hover:bg-green-700 transition-colors"
          >
            Download by Department
          </button>
          <button
            onClick={() => handleExport('member')}
            className="bg-blue-600 text-white py-2 px-4 rounded-lg font-medium hover:bg-blue-700 transition-colors"
          >
            Download by Member
          </button>
        </div>
        <p className="text-sm text-gray-500 mt-2">
          Export data as Excel files for further analysis
        </p>
      </div>

      {/* Quick Links */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Quick Links</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Link
            href="/admin/submissions"
            className="p-4 border rounded-lg text-center hover:bg-gray-50 transition-colors"
          >
            <div className="font-medium text-gray-900">View Submissions</div>
            <div className="text-sm text-gray-500">See all responses</div>
          </Link>
          <Link
            href="/admin/departments"
            className="p-4 border rounded-lg text-center hover:bg-gray-50 transition-colors"
          >
            <div className="font-medium text-gray-900">Manage Departments</div>
            <div className="text-sm text-gray-500">Add or edit departments</div>
          </Link>
          <Link
            href="/admin/categories"
            className="p-4 border rounded-lg text-center hover:bg-gray-50 transition-colors"
          >
            <div className="font-medium text-gray-900">Manage Categories</div>
            <div className="text-sm text-gray-500">Organize departments</div>
          </Link>
          <Link
            href="/admin/settings"
            className="p-4 border rounded-lg text-center hover:bg-gray-50 transition-colors"
          >
            <div className="font-medium text-gray-900">Settings</div>
            <div className="text-sm text-gray-500">Configure limits</div>
          </Link>
        </div>
      </div>
    </div>
  )
}
