import { prisma } from '@/lib/db'
import { NextRequest, NextResponse } from 'next/server'

export async function POST(request: NextRequest) {
  try {
    const { fullName, phone, email, address, selectedDepartments } = await request.json()

    // Validate required fields (email is optional)
    if (!fullName || !phone || !address) {
      return NextResponse.json({ error: 'Please fill in all required fields' }, { status: 400 })
    }

    if (!selectedDepartments || selectedDepartments.length === 0) {
      return NextResponse.json({ error: 'Please select at least one department' }, { status: 400 })
    }

    // Get max departments setting
    const maxSetting = await prisma.settings.findUnique({
      where: { key: 'maxDepartments' }
    })
    const maxDepartments = parseInt(maxSetting?.value || '3')

    if (selectedDepartments.length > maxDepartments) {
      return NextResponse.json({
        error: `You can only select up to ${maxDepartments} departments`
      }, { status: 400 })
    }

    // Validate category constraints
    const departments = await prisma.department.findMany({
      where: { id: { in: selectedDepartments } },
      include: { category: true }
    })

    // Check for multiple selections in same category
    const categorySelections: Record<number, number> = {}
    for (const dept of departments) {
      if (dept.categoryId !== null) {
        if (categorySelections[dept.categoryId]) {
          return NextResponse.json({
            error: 'You can only select one department from each category'
          }, { status: 400 })
        }
        categorySelections[dept.categoryId] = dept.id
      }
    }

    // Create member and department associations
    const member = await prisma.member.create({
      data: {
        fullName,
        phone,
        email: email || '',
        address,
        departments: {
          create: selectedDepartments.map((deptId: number) => ({
            departmentId: deptId
          }))
        }
      },
      include: {
        departments: {
          include: { department: true }
        }
      }
    })

    return NextResponse.json({ success: true, member })
  } catch (error) {
    console.error('Error submitting form:', error)
    return NextResponse.json({ error: 'Submission failed' }, { status: 500 })
  }
}
