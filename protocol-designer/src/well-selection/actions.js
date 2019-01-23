// @flow
import {createAction} from 'redux-actions'
import type {StepFieldName} from '../form-types'
import type {Wells} from '../labware-ingred/types'
import type {Channels} from '@opentrons/components'

// ===== Preselect / select wells in plate

// these actions all use PRIMARY WELLS (see reducers for definition)
const _wellSelectPayloadMapper = (args: Wells): Wells => args

export const highlightWells = createAction(
  'HIGHLIGHT_WELLS',
  _wellSelectPayloadMapper
)

export const selectWells = createAction(
  'SELECT_WELLS',
  (wells: Wells) => wells
)

export const deselectWells = createAction(
  'DESELECT_WELLS',
  _wellSelectPayloadMapper
)

export const deselectAllWells = createAction(
  'DESELECT_ALL_WELLS'
)

// Well selection modal
export type OpenWellSelectionModalPayload = {
  labwareId: string,
  pipetteId: string,
  formFieldAccessor: StepFieldName, // TODO: BC rename this 'name'
  pipetteChannels?: ?Channels,
  labwareName?: string,
}
