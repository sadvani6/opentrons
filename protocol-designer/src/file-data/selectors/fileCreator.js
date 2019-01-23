// @flow
import {createSelector} from 'reselect'
import mapValues from 'lodash/mapValues'
import {getPropertyAllPipettes} from '@opentrons/shared-data'
import {getFileMetadata} from './fileFields'
import {getInitialRobotState, getRobotStateTimeline} from './commands'
import {selectors as dismissSelectors} from '../../dismiss'
import {selectors as ingredSelectors} from '../../labware-ingred/selectors'
import {selectors as stepFormSelectors} from '../../step-forms'
import {
  DEFAULT_MM_FROM_BOTTOM_ASPIRATE,
  DEFAULT_MM_FROM_BOTTOM_DISPENSE,
  DEFAULT_MM_TOUCH_TIP_OFFSET_FROM_TOP,
} from '../../constants'
import type {BaseState} from '../../types'
import type {ProtocolFile, FilePipette, FileLabware} from '../../file-types'
import type {LabwareData, PipetteData} from '../../step-generation'

// TODO LATER Ian 2018-02-28 deal with versioning
const protocolSchemaVersion = '1.0.0'
const applicationVersion = process.env.OT_PD_VERSION || 'unknown version'

// Internal release date: this should never be read programatically,
// it just helps us humans quickly identify what build a user was using
// when we look at saved protocols (without requiring us to trace thru git logs)
const _internalAppBuildDate = process.env.OT_PD_BUILD_DATE

const executionDefaults = {
  'aspirate-flow-rate': getPropertyAllPipettes('defaultAspirateFlowRate'),
  'dispense-flow-rate': getPropertyAllPipettes('defaultDispenseFlowRate'),
  'aspirate-mm-from-bottom': DEFAULT_MM_FROM_BOTTOM_ASPIRATE,
  'dispense-mm-from-bottom': DEFAULT_MM_FROM_BOTTOM_DISPENSE,
  'touch-tip-mm-from-top': DEFAULT_MM_TOUCH_TIP_OFFSET_FROM_TOP,
}

export const createFile: BaseState => ProtocolFile = createSelector(
  getFileMetadata,
  getInitialRobotState,
  getRobotStateTimeline,
  dismissSelectors.getAllDismissedWarnings,
  ingredSelectors.getLiquidGroupsById,
  ingredSelectors.getLiquidsByLabwareId,
  stepFormSelectors.getSavedStepForms,
  stepFormSelectors.getOrderedStepIds,
  stepFormSelectors.getPipetteEntities,
  ingredSelectors.getLabwareNicknamesById,
  (
    fileMetadata,
    initialRobotState,
    robotStateTimeline,
    dismissedWarnings,
    ingredients,
    ingredLocations,
    savedStepForms,
    orderedStepIds,
    pipetteInvariantProperties,
    labwareNamesById,
  ) => {
    const {author, description, created} = fileMetadata
    const name = fileMetadata['protocol-name'] || 'untitled'
    const lastModified = fileMetadata['last-modified']

    const pipettes = mapValues(
      initialRobotState.pipettes,
      (pipette: PipetteData): FilePipette => ({
        mount: pipette.mount,
        // TODO: Ian 2018-11-06 'model' is for backwards compatibility with old API version
        // (JSON executor used to expect versioned model).
        // Drop this "model" when we do breaking change (see TODO in protocol-schema.json)
        model: pipette.name + '_v1.3',
        name: pipette.name,
      })
    )

    const labware = mapValues(
      initialRobotState.labware,
      (l: LabwareData, labwareId: string): FileLabware => ({
        slot: l.slot,
        'display-name': labwareNamesById[labwareId],
        model: l.type,
      })
    )

    // TODO: Ian 2018-07-10 allow user to save steps in JSON file, even if those
    // step never have saved forms.
    // (We could just export the `steps` reducer, but we've sunset it)
    const savedOrderedStepIds = orderedStepIds.filter(stepId => savedStepForms[stepId])

    return {
      'protocol-schema': protocolSchemaVersion,

      metadata: {
        'protocol-name': name,
        author,
        description,
        created,
        'last-modified': lastModified,

        // TODO LATER
        category: null,
        subcategory: null,
        tags: [],
      },

      'default-values': executionDefaults,

      'designer-application': {
        'application-name': 'opentrons/protocol-designer',
        'application-version': applicationVersion,
        _internalAppBuildDate,
        data: {
          pipetteTiprackAssignments: mapValues(
            pipetteInvariantProperties,
            (p: $Values<typeof pipetteInvariantProperties>): ?string => p.tiprackModel
          ),
          dismissedWarnings,
          ingredients,
          ingredLocations,
          savedStepForms,
          orderedStepIds: savedOrderedStepIds,
        },
      },

      robot: {
        model: 'OT-2 Standard',
      },

      pipettes,
      labware,

      procedure: robotStateTimeline.timeline.map((timelineItem, i) => ({
        annotation: {
          name: `TODO Name ${i}`,
          description: 'todo description',
        },
        subprocedure: timelineItem.commands.reduce((acc, c) => [...acc, c], []),
      })),
    }
  }
)
