import { ChevronRight } from 'lucide-react'
import { Link } from 'react-router-dom'

interface StudyBreadcrumbProps {
  patientId: string
  patientName: string
  studyId: string
}

export function StudyBreadcrumb({ patientId, patientName, studyId }: StudyBreadcrumbProps) {
  return (
    <nav aria-label="Breadcrumb" className="flex items-center gap-1 text-body3 text-gray-600">
      <Link
        to="/patients"
        className="hover:text-primary-500 hover:underline focus-visible:text-primary-500 focus-visible:underline focus-visible:outline-none"
      >
        Pacientes
      </Link>
      <ChevronRight className="size-3.5 text-gray-300" aria-hidden />
      <Link
        to={`/patients/${patientId}`}
        className="hover:text-primary-500 hover:underline focus-visible:text-primary-500 focus-visible:underline focus-visible:outline-none"
      >
        {patientName}
      </Link>
      <ChevronRight className="size-3.5 text-gray-300" aria-hidden />
      <span className="text-gray-900" aria-current="page">
        Estudio {studyId}
      </span>
    </nav>
  )
}
