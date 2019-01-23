// @flow
import * as React from 'react'
import {connect} from 'react-redux'
import {selectors} from '../labware-ingred/selectors'
import * as wellSelectionSelectors from '../top-selectors/well-contents'
import {removeWellsContents} from '../labware-ingred/actions'
import type {Dispatch} from 'redux'
import type {BaseState} from '../types'

import IngredientsList from '../components/IngredientsList'

type Props = React.ElementProps<typeof IngredientsList>

type DP = {
  removeWellsContents: $ElementType<Props, 'removeWellsContents'>,
}

type SP = $Diff<Props, DP> & {_labwareId: ?string}

function mapStateToProps (state: BaseState): SP {
  const container = selectors.getSelectedLabware(state)
  const _labwareId = container && container.id

  return {
    liquidGroupsById: selectors.getLiquidGroupsById(state),
    labwareWellContents: (container && selectors.getLiquidsByLabwareId(state)[container.id]) || {},
    selectedIngredientGroupId: wellSelectionSelectors.getSelectedWellsCommonIngredId(state),
    selected: false,
    _labwareId,
  }
}

function mergeProps (stateProps: SP, dispatchProps: {dispatch: Dispatch<*>}): Props {
  const {dispatch} = dispatchProps
  const {_labwareId, ...passThruProps} = stateProps
  return {
    ...passThruProps,
    removeWellsContents: (args) => dispatch(removeWellsContents({...args, labwareId: _labwareId})),
  }
}

export default connect(mapStateToProps, null, mergeProps)(IngredientsList)
