// @flow
import assert from 'assert'
import {createSelector} from 'reselect'
import last from 'lodash/last'
import mapValues from 'lodash/mapValues'
import reduce from 'lodash/reduce'
import takeWhile from 'lodash/takeWhile'
import uniqBy from 'lodash/uniqBy'
import {getIsTiprack, getPipetteNameSpecs} from '@opentrons/shared-data'
import type {BaseState, Selector} from '../../types'
import {getAllWellsForLabware} from '../../constants'
import * as StepGeneration from '../../step-generation'
import {selectors as stepFormSelectors} from '../../step-forms'
import {selectors as labwareIngredSelectors} from '../../labware-ingred/selectors'
import type {StepIdType} from '../../form-types'
import type {LabwareOnDeck, PipetteOnDeck} from '../../step-forms'

const all96Tips = reduce(
  StepGeneration.tiprackWellNamesFlat,
  (acc: {[string]: boolean}, wellName: string) => ({...acc, [wellName]: true}),
  {}
)

// NOTE this just adds missing well keys to the labware-ingred 'deck setup' liquid state
export const getLabwareLiquidState: Selector<StepGeneration.LabwareLiquidState> = createSelector(
  labwareIngredSelectors.getLiquidsByLabwareId,
  labwareIngredSelectors.getLabwareById,
  (ingredLocations, allLabware) => {
    const allLabwareIds: Array<string> = Object.keys(allLabware)
    return allLabwareIds.reduce((
      acc: StepGeneration.LabwareLiquidState,
      labwareId
    ): StepGeneration.LabwareLiquidState => {
      const labwareType = allLabware[labwareId] && allLabware[labwareId].type
      const allWells = labwareType ? getAllWellsForLabware(labwareType) : []
      const liquidStateForLabwareAllWells = allWells.reduce(
        (innerAcc: StepGeneration.SingleLabwareLiquidState, well) => ({
          ...innerAcc,
          [well]: (ingredLocations[labwareId] && ingredLocations[labwareId][well]) || {},
        }),
        {}
      )
      return {
        ...acc,
        [labwareId]: liquidStateForLabwareAllWells,
      }
    }, {})
  }
)

type TipState = $PropertyType<StepGeneration.RobotState, 'tipState'>
type TiprackTipState = $PropertyType<TipState, 'tipracks'>
export const getInitialRobotState: BaseState => StepGeneration.RobotState = createSelector(
  stepFormSelectors.getInitialDeckSetup,
  getLabwareLiquidState,
  (initialDeckSetup, labwareLiquidState) => {
    const labware = mapValues(initialDeckSetup.labware, (l: LabwareOnDeck, id: string): StepGeneration.LabwareData => ({
      type: l.type,
      slot: l.slot,
    }))

    const pipettes = mapValues(initialDeckSetup.pipettes, (p: PipetteOnDeck, id: string): StepGeneration.PipetteData => {
      const pipetteSpecs = getPipetteNameSpecs(p.name)
      if (!pipetteSpecs) {
        // this should never happen this far along
        throw new Error(`no pipette spec for ${p && p.name}`)
      }
      return {
        mount: p.mount,
        name: p.name,
        tiprackModel: p.tiprackModel,
      }
    })

    const tipracks: TiprackTipState = reduce(
      labware,
      (acc: TiprackTipState, labwareData: StepGeneration.LabwareData, labwareId: string) => {
        const isTiprack = getIsTiprack(labwareData.type)
        if (labwareData.type && isTiprack) {
          return {
            ...acc,
            // TODO LATER Ian 2018-05-18 use shared-data wells instead of assuming 96 tips?
            [labwareId]: {...all96Tips},
          }
        }
        return acc
      },
      {})

    type PipetteTipState = {[pipetteId: string]: boolean}
    const pipetteTipState: PipetteTipState = reduce(
      pipettes,
      (acc: PipetteTipState, pipetteData: StepGeneration.PipetteData, pipetteId: string) =>
        ({
          ...acc,
          [pipetteId]: false, // start with no tips
        }),
      {})

    const pipetteLiquidState = reduce(
      pipettes,
      (acc, pipetteData: StepGeneration.PipetteData, pipetteId: string) => {
        const pipetteSpec = getPipetteNameSpecs(pipetteData.name)
        assert(pipetteSpec, `expected pipette spec for ${pipetteId} ${pipetteData.name}, could not make pipetteLiquidState for initial frame correctly`)
        const channels = pipetteSpec ? pipetteSpec.channels : 1
        assert(channels === 1 || channels === 8, 'expected pipette with 1 or 8 channels')
        return {
          ...acc,
          [pipetteId]: (channels > 1)
            ? {'0': {}, '1': {}, '2': {}, '3': {}, '4': {}, '5': {}, '6': {}, '7': {}}
            : {'0': {}},
        }
      }, {})

    return {
      pipettes,
      labware,
      tipState: {
        tipracks,
        pipettes: pipetteTipState,
      },
      liquidState: {
        pipettes: pipetteLiquidState,
        labware: labwareLiquidState,
      },
    }
  }
)
function compoundCommandCreatorFromStepArgs (stepArgs: StepGeneration.CommandCreatorData): ?StepGeneration.CompoundCommandCreator {
  switch (stepArgs.stepType) {
    case 'consolidate':
      return StepGeneration.consolidate(stepArgs)
    case 'transfer':
      return StepGeneration.transfer(stepArgs)
    case 'distribute':
      return StepGeneration.distribute(stepArgs)
    case 'mix':
      return StepGeneration.mix(stepArgs)
    default:
      return null
  }
}

// exposes errors and last valid robotState
export const getRobotStateTimeline: Selector<StepGeneration.Timeline> = createSelector(
  stepFormSelectors.getArgsAndErrorsByStepId,
  stepFormSelectors.getOrderedStepIds,
  getInitialRobotState,
  (allStepArgsAndErrors, orderedStepIds, initialRobotState) => {
    const allStepArgs: Array<StepGeneration.CommandCreatorData | null> = orderedStepIds.map(stepId => {
      return (allStepArgsAndErrors[stepId] && allStepArgsAndErrors[stepId].stepArgs) || null
    })

    // TODO: Ian 2018-06-14 `takeWhile` isn't inferring the right type
    // $FlowFixMe
    const continuousStepArgs: Array<StepGeneration.CommandCreatorData> = takeWhile(
      allStepArgs,
      stepArgs => stepArgs
    )

    const commandCreators = continuousStepArgs.reduce(
      (acc: Array<StepGeneration.CommandCreator>, stepArgs, stepIndex) => {
        const {stepType} = stepArgs
        let reducedCommandCreator = null

        if (stepArgs.stepType === 'pause') {
          reducedCommandCreator = StepGeneration.delay(stepArgs)
        } else { // NOTE: compound return an array of command creators, atomic steps only return one command creator
          const compoundCommandCreator: ?StepGeneration.CompoundCommandCreator = compoundCommandCreatorFromStepArgs(stepArgs)
          reducedCommandCreator = compoundCommandCreator && StepGeneration.reduceCommandCreators(compoundCommandCreator(initialRobotState))
        }
        if (!reducedCommandCreator) {
          // TODO Ian 2018-05-08 use assert
          console.warn(`StepType "${stepType}" not yet implemented`)
          return acc
        }

        // Drop tips eagerly, per pipette
        // NOTE: this assumes all step forms that use a pipette have both
        // 'pipette' and 'changeTip' fields (and they're not named something else).
        const pipetteId = stepArgs.pipette
        if (pipetteId) {
          const nextStepArgsForPipette = continuousStepArgs
            .slice(stepIndex + 1)
            .find(stepArgs => stepArgs.pipette === pipetteId)

          const willReuseTip = nextStepArgsForPipette && nextStepArgsForPipette.changeTip === 'never'
          if (!willReuseTip) {
            return [
              ...acc,
              StepGeneration.reduceCommandCreators([
                reducedCommandCreator,
                StepGeneration.dropTip(pipetteId),
              ]),
            ]
          }
        }

        return [...acc, reducedCommandCreator]
      }, [])

    const timeline = StepGeneration.commandCreatorsTimeline(commandCreators)(initialRobotState)

    return timeline
  }
)

type WarningsPerStep = {[stepId: number | string]: ?Array<StepGeneration.CommandCreatorWarning>}
export const timelineWarningsPerStep: Selector<WarningsPerStep> = createSelector(
  stepFormSelectors.getOrderedStepIds,
  getRobotStateTimeline,
  (orderedStepIds, timeline) => timeline.timeline.reduce((acc: WarningsPerStep, frame, timelineIndex) => {
    const stepId = orderedStepIds[timelineIndex]

    // remove warnings of duplicate 'type'. chosen arbitrarily
    return {
      ...acc,
      [stepId]: uniqBy(frame.warnings, w => w.type),
    }
  }, {})
)

export const getErrorStepId: Selector<?StepIdType> = createSelector(
  stepFormSelectors.getOrderedStepIds,
  getRobotStateTimeline,
  (orderedStepIds, timeline) => {
    const hasErrors = timeline.errors && timeline.errors.length > 0
    if (hasErrors) {
      // the frame *after* the last frame in the timeline is the error-throwing one
      const errorIndex = timeline.timeline.length
      const errorStepId = orderedStepIds[errorIndex]
      return errorStepId
    }
    return null
  }
)

export const lastValidRobotState: Selector<StepGeneration.RobotState> = createSelector(
  getRobotStateTimeline,
  getInitialRobotState,
  (timeline, initialRobotState) => {
    const lastTimelineFrame = last(timeline.timeline)
    return (lastTimelineFrame && lastTimelineFrame.robotState) || initialRobotState
  }
)
