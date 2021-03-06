// @flow
import * as React from 'react'
import {connect} from 'react-redux'
import isEmpty from 'lodash/isEmpty'
import type {BaseState, ThunkDispatch} from '../types'

import type {SubstepIdentifier} from '../steplist/types'
import * as substepSelectors from '../top-selectors/substeps'
import {selectors as dismissSelectors} from '../dismiss'
import {selectors as stepFormSelectors} from '../step-forms'
import {selectors as stepsSelectors, actions as stepsActions} from '../ui/steps'
import {selectors as fileDataSelectors} from '../file-data'
import {selectors as labwareIngredSelectors} from '../labware-ingred/selectors'
import {selectors as uiLabwareSelectors} from '../ui/labware'
import StepItem from '../components/steplist/StepItem' // TODO Ian 2018-05-10 why is importing StepItem from index.js not working?

type Props = React.ElementProps<typeof StepItem>

type OP = {
  stepId: $PropertyType<Props, 'stepId'>,
  stepNumber: $PropertyType<Props, 'stepNumber'>,
}

type SP = {|
  stepType: $PropertyType<Props, 'stepType'>,
  title: $PropertyType<Props, 'title'>,
  description: $PropertyType<Props, 'description'>,
  rawForm: $PropertyType<Props, 'rawForm'>,
  substeps: $PropertyType<Props, 'substeps'>,
  collapsed: $PropertyType<Props, 'collapsed'>,
  error: $PropertyType<Props, 'error'>,
  selected: $PropertyType<Props, 'selected'>,
  hovered: $PropertyType<Props, 'hovered'>,
  hoveredSubstep: $PropertyType<Props, 'hoveredSubstep'>,
  labwareNicknamesById: $PropertyType<Props, 'labwareNicknamesById'>,
  labwareTypesById: $PropertyType<Props, 'labwareTypesById'>,
  ingredNames: $PropertyType<Props, 'ingredNames'>,
|}

type DP = $Diff<$Diff<Props, SP>, OP>

function mapStateToProps (state: BaseState, ownProps: OP): SP {
  const {stepId} = ownProps
  const allSteps = stepFormSelectors.getAllSteps(state)

  const hoveredSubstep = stepsSelectors.getHoveredSubstep(state)
  const hoveredStep = stepsSelectors.getHoveredStepId(state)
  const selected = stepsSelectors.getSelectedStepId(state) === stepId
  const collapsed = stepsSelectors.getCollapsedSteps(state)[stepId]
  const argsAndErrorsByStepId = stepFormSelectors.getArgsAndErrorsByStepId(state)
  const formAndFieldErrors = argsAndErrorsByStepId[stepId] && argsAndErrorsByStepId[stepId].errors
  const hasError = fileDataSelectors.getErrorStepId(state) === stepId || !isEmpty(formAndFieldErrors)
  const warnings = dismissSelectors.getTimelineWarningsPerStep(state)[stepId]
  const hasWarnings = warnings && warnings.length > 0

  const showErrorState = hasError || hasWarnings
  const step = allSteps[stepId]

  return {
    stepType: step.stepType,
    title: step.title,
    description: step.description,
    rawForm: step.formData,
    substeps: substepSelectors.allSubsteps(state)[stepId],
    hoveredSubstep,
    collapsed,
    selected,
    error: showErrorState,

    // no double-highlighting: whole step is only "hovered" when
    // user is not hovering on substep.
    hovered: (hoveredStep === stepId) && !hoveredSubstep,

    labwareNicknamesById: uiLabwareSelectors.getLabwareNicknamesById(state),
    labwareTypesById: stepFormSelectors.getLabwareTypesById(state),
    ingredNames: labwareIngredSelectors.getLiquidNamesById(state),
  }
}

function mapDispatchToProps (dispatch: ThunkDispatch<*>): DP {
  return {
    highlightSubstep: (payload: SubstepIdentifier) => dispatch(stepsActions.hoverOnSubstep(payload)),
    selectStep: (stepId) => dispatch(stepsActions.selectStep(stepId)),
    toggleStepCollapsed: (stepId) => dispatch(stepsActions.toggleStepCollapsed(stepId)),
    highlightStep: (stepId) => dispatch(stepsActions.hoverOnStep(stepId)),
    unhighlightStep: (stepId) => dispatch(stepsActions.hoverOnStep(null)),
  }
}

export default connect(mapStateToProps, mapDispatchToProps)(StepItem)
