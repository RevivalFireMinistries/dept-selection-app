'use client'

import { useState, useEffect } from 'react'

interface Category {
  id: number
  name: string
  departments: { id: number; name: string }[]
}

export default function CategoriesPage() {
  const [categories, setCategories] = useState<Category[]>([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [name, setName] = useState('')

  useEffect(() => {
    fetchCategories()
  }, [])

  const fetchCategories = async () => {
    try {
      const res = await fetch('/api/categories')
      const data = await res.json()
      setCategories(Array.isArray(data) ? data : [])
    } catch (error) {
      console.error('Error fetching categories:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    const payload = {
      name,
      ...(editingId && { id: editingId })
    }

    try {
      const res = await fetch('/api/categories', {
        method: editingId ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })

      if (res.ok) {
        setName('')
        setShowForm(false)
        setEditingId(null)
        fetchCategories()
      }
    } catch (error) {
      console.error('Error saving category:', error)
    }
  }

  const handleEdit = (cat: Category) => {
    setName(cat.name)
    setEditingId(cat.id)
    setShowForm(true)
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Are you sure? Departments in this category will become uncategorized.')) return

    try {
      await fetch(`/api/categories?id=${id}`, { method: 'DELETE' })
      fetchCategories()
    } catch (error) {
      console.error('Error deleting category:', error)
    }
  }

  if (loading) {
    return <div className="text-center py-8">Loading...</div>
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold text-gray-900">Categories</h1>
        <button
          onClick={() => {
            setName('')
            setEditingId(null)
            setShowForm(!showForm)
          }}
          className="bg-blue-600 text-white py-2 px-4 rounded-lg font-medium hover:bg-blue-700 transition-colors"
        >
          {showForm ? 'Cancel' : 'Add Category'}
        </button>
      </div>

      <p className="text-gray-600">
        Categories group departments together. Members can only select one department from each category.
      </p>

      {showForm && (
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-lg font-semibold mb-4">
            {editingId ? 'Edit Category' : 'New Category'}
          </h2>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Category Name
              </label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="e.g., Music Ministry"
                required
              />
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

      <div className="space-y-4">
        {categories.length === 0 ? (
          <div className="bg-white rounded-lg shadow p-8 text-center text-gray-500">
            No categories yet. Create one to group departments.
          </div>
        ) : (
          categories.map(cat => (
            <div key={cat.id} className="bg-white rounded-lg shadow p-4">
              <div className="flex justify-between items-start">
                <div>
                  <h3 className="font-semibold text-gray-900">{cat.name}</h3>
                  <div className="text-sm text-gray-500 mt-1">
                    {cat.departments.length} department(s)
                  </div>
                  {cat.departments.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {cat.departments.map(dept => (
                        <span
                          key={dept.id}
                          className="text-xs bg-gray-100 text-gray-600 px-2 py-1 rounded"
                        >
                          {dept.name}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <div className="space-x-2">
                  <button
                    onClick={() => handleEdit(cat)}
                    className="text-blue-600 hover:text-blue-800 text-sm"
                  >
                    Edit
                  </button>
                  <button
                    onClick={() => handleDelete(cat.id)}
                    className="text-red-600 hover:text-red-800 text-sm"
                  >
                    Delete
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
