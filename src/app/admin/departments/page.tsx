'use client'

import { useState, useEffect } from 'react'

interface Category {
  id: number
  name: string
}

interface Department {
  id: number
  name: string
  categoryId: number | null
  category?: Category
}

export default function DepartmentsPage() {
  const [departments, setDepartments] = useState<Department[]>([])
  const [categories, setCategories] = useState<Category[]>([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [formData, setFormData] = useState({ name: '', categoryId: '' })

  useEffect(() => {
    fetchData()
  }, [])

  const fetchData = async () => {
    try {
      const [deptsRes, catsRes] = await Promise.all([
        fetch('/api/departments'),
        fetch('/api/categories')
      ])

      const deptsData = await deptsRes.json()
      const catsData = await catsRes.json()

      // Combine all departments
      const allDepts: Department[] = []
      if (deptsData.categories) {
        deptsData.categories.forEach((cat: any) => {
          cat.departments.forEach((dept: any) => {
            allDepts.push({ ...dept, category: { id: cat.id, name: cat.name } })
          })
        })
      }
      if (deptsData.uncategorized) {
        allDepts.push(...deptsData.uncategorized)
      }

      setDepartments(allDepts)
      setCategories(Array.isArray(catsData) ? catsData : [])
    } catch (error) {
      console.error('Error fetching data:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    const payload = {
      name: formData.name,
      categoryId: formData.categoryId ? parseInt(formData.categoryId) : null,
      ...(editingId && { id: editingId })
    }

    try {
      const res = await fetch('/api/departments', {
        method: editingId ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })

      if (res.ok) {
        setFormData({ name: '', categoryId: '' })
        setShowForm(false)
        setEditingId(null)
        fetchData()
      }
    } catch (error) {
      console.error('Error saving department:', error)
    }
  }

  const handleEdit = (dept: Department) => {
    setFormData({
      name: dept.name,
      categoryId: dept.categoryId?.toString() || ''
    })
    setEditingId(dept.id)
    setShowForm(true)
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Are you sure you want to delete this department?')) return

    try {
      await fetch(`/api/departments?id=${id}`, { method: 'DELETE' })
      fetchData()
    } catch (error) {
      console.error('Error deleting department:', error)
    }
  }

  if (loading) {
    return <div className="text-center py-8">Loading...</div>
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold text-gray-900">Departments</h1>
        <button
          onClick={() => {
            setFormData({ name: '', categoryId: '' })
            setEditingId(null)
            setShowForm(!showForm)
          }}
          className="bg-blue-600 text-white py-2 px-4 rounded-lg font-medium hover:bg-blue-700 transition-colors"
        >
          {showForm ? 'Cancel' : 'Add Department'}
        </button>
      </div>

      {showForm && (
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-lg font-semibold mb-4">
            {editingId ? 'Edit Department' : 'New Department'}
          </h2>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Department Name
              </label>
              <input
                type="text"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                required
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Category (Optional)
              </label>
              <select
                value={formData.categoryId}
                onChange={(e) => setFormData({ ...formData, categoryId: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">No Category</option>
                {categories.map(cat => (
                  <option key={cat.id} value={cat.id}>{cat.name}</option>
                ))}
              </select>
            </div>
            <button
              type="submit"
              className="bg-green-600 text-white py-2 px-4 rounded-lg font-medium hover:bg-green-700 transition-colors"
            >
              {editingId ? 'Update' : 'Create'}
            </button>
          </form>
        </div>
      )}

      <div className="bg-white rounded-lg shadow overflow-hidden">
        <table className="w-full">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left text-sm font-medium text-gray-600">Name</th>
              <th className="px-4 py-3 text-left text-sm font-medium text-gray-600">Category</th>
              <th className="px-4 py-3 text-right text-sm font-medium text-gray-600">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {departments.map(dept => (
              <tr key={dept.id} className="hover:bg-gray-50">
                <td className="px-4 py-3 text-gray-900">{dept.name}</td>
                <td className="px-4 py-3 text-gray-500">
                  {dept.category?.name || '-'}
                </td>
                <td className="px-4 py-3 text-right space-x-2">
                  <button
                    onClick={() => handleEdit(dept)}
                    className="text-blue-600 hover:text-blue-800 text-sm"
                  >
                    Edit
                  </button>
                  <button
                    onClick={() => handleDelete(dept.id)}
                    className="text-red-600 hover:text-red-800 text-sm"
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
