// @flow
import assert from 'assert'
import round from 'lodash/round'
import uniq from 'lodash/uniq'
import {getPipetteCapacity} from '../../../pipettes/pipetteData'
import {getWellSetForMultichannel} from '../../../well-selection/utils'
import type {PipetteChannels} from '@opentrons/shared-data'
import type {FormPatch} from '../../actions/types'
import type {FormData} from '../../../form-types'
import type {PipetteEntities} from '../../../step-forms'

export function chainPatchUpdaters (initialPatch: FormPatch, fns: Array<(FormPatch => FormPatch)>): FormPatch {
  return fns.reduce((patchAcc: FormPatch, fn) => {
    return fn(patchAcc)
  }, initialPatch)
}

// given an array of primary wells (for a multichannel), return all unique wells
// included in that set. Used to convert multi to single.
export function getAllWellsFromPrimaryWells (
  primaryWells: ?Array<string>,
  labwareType: ?string
): Array<string> {
  if (!labwareType || !primaryWells) {
    return []
  }

  const _labwareType = labwareType // TODO Ian 2018-05-04 remove this weird flow workaround

  const allWells = primaryWells.reduce((acc: Array<string>, well: string) => {
    const nextWellSet = getWellSetForMultichannel(_labwareType, well)
    // filter out any nulls (but you shouldn't get any)
    return (nextWellSet) ? [...acc, ...nextWellSet] : acc
  }, [])

  // remove duplicates (eg trough: [A1, A1, A1, A1, A1, A1, A1, A1] -> [A1])
  return uniq(allWells)
}

export function getChannels (pipetteId: string, pipetteEntities: PipetteEntities): ?PipetteChannels {
  const pipette: ?* = pipetteEntities[pipetteId]
  if (!pipette) {
    return null
  }
  return pipette.spec.channels
}

export const DISPOSAL_VOL_DIGITS = 1

export function getMaxDisposalVolumeForMultidispense (rawForm: ?FormData, pipetteEntities: PipetteEntities): ?number {
  // calculate max disposal volume for given volume & pipette. Might be negative!
  if (!rawForm) return null
  assert(rawForm.path === 'multiDispense', `getMaxDisposalVolumeForMultidispense expected multiDispense, got path ${rawForm.path}`)
  const volume = Number(rawForm.volume)
  const pipetteEntity = pipetteEntities[rawForm.pipette]
  const pipetteCapacity = getPipetteCapacity(pipetteEntity)
  return round(pipetteCapacity - (volume * 2), DISPOSAL_VOL_DIGITS)
}

// Ensures that 2x volume can fit in pipette
// NOTE: ensuring that disposalVolume_volume will not exceed pipette capacity
// is responsibility of dependentFieldsUpdateMoveLiquid's clamp fn
export function volumeInCapacityForMulti (
  rawForm: FormData,
  pipetteEntities: PipetteEntities
): boolean {
  const volume = Number(rawForm.volume)
  assert(
    rawForm.pipette in pipetteEntities,
    `volumeInCapacityForMulti expected pipette ${rawForm.pipette} to be in pipetteEntities`
  )
  const pipetteEntity = pipetteEntities[rawForm.pipette]
  const pipetteCapacity = pipetteEntity && getPipetteCapacity(pipetteEntity)

  return (
    volume > 0 &&
    pipetteCapacity > 0 &&
    volume * 2 <= pipetteCapacity
  )
}
