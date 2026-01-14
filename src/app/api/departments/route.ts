import { prisma } from '@/lib/db'
import { NextRequest, NextResponse } from 'next/server'

export async function GET() {
  try {
    // Get categories with their departments
    const categories = await prisma.category.findMany({
      include: {
        departments: true
      },
      orderBy: { name: 'asc' }
    })

    // Get uncategorized departments
    const uncategorized = await prisma.department.findMany({
      where: { categoryId: null },
      orderBy: { name: 'asc' }
    })

    return NextResponse.json({ categories, uncategorized })
  } catch (error) {
    console.error('Error fetching departments:', error)
    return NextResponse.json({ error: 'Failed to fetch departments' }, { status: 500 })
  }
}

export async function POST(request: NextRequest) {
  try {
    const { name, categoryId } = await request.json()

    if (!name) {
      return NextResponse.json({ error: 'Name is required' }, { status: 400 })
    }

    const department = await prisma.department.create({
      data: {
        name,
        categoryId: categoryId || null
      }
    })

    return NextResponse.json(department)
  } catch (error) {
    console.error('Error creating department:', error)
    return NextResponse.json({ error: 'Failed to create department' }, { status: 500 })
  }
}

export async function PUT(request: NextRequest) {
  try {
    const { id, name, categoryId } = await request.json()

    if (!id || !name) {
      return NextResponse.json({ error: 'ID and name are required' }, { status: 400 })
    }

    const department = await prisma.department.update({
      where: { id },
      data: {
        name,
        categoryId: categoryId || null
      }
    })

    return NextResponse.json(department)
  } catch (error) {
    console.error('Error updating department:', error)
    return NextResponse.json({ error: 'Failed to update department' }, { status: 500 })
  }
}

export async function DELETE(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url)
    const id = parseInt(searchParams.get('id') || '')

    if (!id) {
      return NextResponse.json({ error: 'ID is required' }, { status: 400 })
    }

    await prisma.department.delete({ where: { id } })

    return NextResponse.json({ success: true })
  } catch (error) {
    console.error('Error deleting department:', error)
    return NextResponse.json({ error: 'Failed to delete department' }, { status: 500 })
  }
}
