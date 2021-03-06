// @flow
import range from 'lodash/range'
import isEmpty from 'lodash/isEmpty'
import {mergeLiquid, splitLiquid, getWellsForTips, totalVolume} from '../utils'
import {getPipetteSpecFromId} from '../robotStateSelectors'
import * as warningCreators from '../warningCreators'
import type {
  RobotState,
  SingleLabwareLiquidState,
  CommandCreatorWarning,
  AspirateDispenseArgs,
  RobotStateAndWarnings,
} from '../types'

export default function getNextRobotStateAndWarningsForAspDisp (
  args: AspirateDispenseArgs,
  prevRobotState: RobotState
): RobotStateAndWarnings {
  const {pipette: pipetteId, volume, labware: labwareId, well} = args

  const {liquidState: prevLiquidState} = prevRobotState
  const pipetteSpec = getPipetteSpecFromId(pipetteId, prevRobotState)
  const labwareType = prevRobotState.labware[labwareId].type

  const {wellsForTips} = getWellsForTips(pipetteSpec.channels, labwareType, well)

  // Blend tip's liquid contents (if any) with liquid of the source
  // to update liquid state in all pipette tips
  type PipetteLiquidStateAcc = {
    pipetteLiquidState: SingleLabwareLiquidState,
    pipetteWarnings: Array<CommandCreatorWarning>,
  }
  const {pipetteLiquidState, pipetteWarnings} = range(pipetteSpec.channels).reduce(
    (acc: PipetteLiquidStateAcc, tipIndex) => {
      const prevTipLiquidState = prevLiquidState.pipettes[pipetteId][tipIndex.toString()]
      const prevSourceLiquidState = prevLiquidState.labware[labwareId][wellsForTips[tipIndex]]

      const newLiquidFromWell = splitLiquid(
        volume,
        prevSourceLiquidState
      ).dest

      let nextWarnings = []
      if (isEmpty(prevSourceLiquidState)) {
        nextWarnings = [...nextWarnings, warningCreators.aspirateFromPristineWell()]
      } else if (volume > totalVolume(prevSourceLiquidState)) {
        nextWarnings = [...nextWarnings, warningCreators.aspirateMoreThanWellContents()]
      }

      return {
        pipetteLiquidState: {
          ...acc.pipetteLiquidState,
          [tipIndex]: mergeLiquid(
            prevTipLiquidState,
            newLiquidFromWell
          ),
        },
        pipetteWarnings: [...acc.pipetteWarnings, ...nextWarnings],
      }
    }, {pipetteLiquidState: {}, pipetteWarnings: []})

  // Remove liquid from source well(s)
  const labwareLiquidState: SingleLabwareLiquidState = {
    ...prevLiquidState.labware[labwareId],
    ...wellsForTips.reduce((acc: SingleLabwareLiquidState, well) => ({
      ...acc,
      [well]: splitLiquid(
        volume,
        // When multiple tips aspirate from 1 well,
        // that volume is sequentially removed, tip by tip
        acc[well] || prevLiquidState.labware[labwareId][well]
      ).source,
    }), {}),
  }

  const nextLiquidState = {
    pipettes: {
      ...prevLiquidState.pipettes,
      [pipetteId]: pipetteLiquidState,
    },
    labware: {
      ...prevLiquidState.labware,
      [labwareId]: labwareLiquidState,
    },
  }

  return {
    robotState: {
      ...prevRobotState,
      liquidState: nextLiquidState,
    },
    warnings: pipetteWarnings,
  }
}
