'use client'

import { useState, useEffect } from 'react'

interface Member {
  id: number
  fullName: string
  phone: string
  email: string
  address: string
  createdAt: string
  departments: {
    department: {
      id: number
      name: string
      category: { name: string } | null
    }
  }[]
}

export default function SubmissionsPage() {
  const [members, setMembers] = useState<Member[]>([])
  const [loading, setLoading] = useState(true)
  const [expandedId, setExpandedId] = useState<number | null>(null)

  useEffect(() => {
    fetchMembers()
  }, [])

  const fetchMembers = async () => {
    try {
      const res = await fetch('/api/members')
      const data = await res.json()
      setMembers(Array.isArray(data) ? data : [])
    } catch (error) {
      console.error('Error fetching members:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Are you sure you want to delete this submission?')) return

    try {
      await fetch(`/api/members?id=${id}`, { method: 'DELETE' })
      setMembers(members.filter(m => m.id !== id))
    } catch (error) {
      console.error('Error deleting member:', error)
    }
  }

  if (loading) {
    return <div className="text-center py-8">Loading...</div>
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold text-gray-900">Submissions</h1>
        <span className="text-gray-500">{members.length} total</span>
      </div>

      {members.length === 0 ? (
        <div className="bg-white rounded-lg shadow p-8 text-center text-gray-500">
          No submissions yet
        </div>
      ) : (
        <div className="space-y-4">
          {members.map(member => (
            <div key={member.id} className="bg-white rounded-lg shadow overflow-hidden">
              <div
                className="p-4 cursor-pointer hover:bg-gray-50"
                onClick={() => setExpandedId(expandedId === member.id ? null : member.id)}
              >
                <div className="flex justify-between items-start">
                  <div>
                    <div className="font-semibold text-gray-900">{member.fullName}</div>
                    <div className="text-sm text-gray-500">{member.email}</div>
                    <div className="text-sm text-gray-500">{member.phone}</div>
                  </div>
                  <div className="text-right">
                    <div className="text-sm text-gray-500">
                      {new Date(member.createdAt).toLocaleDateString()}
                    </div>
                    <div className="text-sm font-medium text-blue-600">
                      {member.departments.length} department(s)
                    </div>
                  </div>
                </div>
              </div>

              {expandedId === member.id && (
                <div className="border-t p-4 bg-gray-50">
                  <div className="mb-4">
                    <div className="text-sm font-medium text-gray-700 mb-1">Address</div>
                    <div className="text-sm text-gray-600">{member.address}</div>
                  </div>

                  <div className="mb-4">
                    <div className="text-sm font-medium text-gray-700 mb-2">Selected Departments</div>
                    <div className="flex flex-wrap gap-2">
                      {member.departments.map(d => (
                        <span
                          key={d.department.id}
                          className="inline-flex items-center px-3 py-1 rounded-full text-sm bg-blue-100 text-blue-800"
                        >
                          {d.department.name}
                          {d.department.category && (
                            <span className="ml-1 text-blue-600 text-xs">
                              ({d.department.category.name})
                            </span>
                          )}
                        </span>
                      ))}
                    </div>
                  </div>

                  <button
                    onClick={() => handleDelete(member.id)}
                    className="text-red-600 text-sm hover:text-red-800"
                  >
                    Delete Submission
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
