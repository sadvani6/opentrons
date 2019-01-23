// @flow
import * as React from 'react'
import {connect} from 'react-redux'
import type {Dispatch} from 'redux'
import cx from 'classnames'
import isEmpty from 'lodash/isEmpty'

import styles from './LiquidPlacementModal.css'

import type {Wells, ContentsByWell} from '../labware-ingred/types'
import {SelectableLabware} from '../components/labware'
import LiquidPlacementForm from '../components/LiquidPlacementForm'
import SingleLabwareWrapper from '../components/SingleLabware'
import WellSelectionInstructions from './WellSelectionInstructions'

import {selectors} from '../labware-ingred/selectors'
import * as wellContentsSelectors from '../top-selectors/well-contents'
import wellSelectionSelectors from '../well-selection/selectors'
import {
  selectWells,
  deselectWells,
} from '../well-selection/actions'

import type {BaseState} from '../types'
import type {WellIngredientNames} from '../steplist'

type SP = {
  selectedWells: Wells,
  wellContents: ContentsByWell,
  containerType: string,
  liquidNamesById: WellIngredientNames,
}
type DP = {
  selectWells: (Wells) => mixed,
  deselectWells: (Wells) => mixed,
}
type Props = SP & DP

type State = {highlightedWells: Wells}

class LiquidPlacementModal extends React.Component<Props, State> {
  state = {highlightedWells: {}}
  constructor (props) {
    super(props)
    this.state = {highlightedWells: {}}
  }

  updateHighlightedWells = (wells: Wells) => {
    this.setState({highlightedWells: wells})
  }

  render () {
    return (
      <div className={cx(styles.liquid_placement_modal, {[styles.expanded]: !isEmpty(this.props.selectedWells)})}>
        <LiquidPlacementForm />

        <SingleLabwareWrapper showLabels>
          <SelectableLabware
            wellContents={this.props.wellContents}
            containerType={this.props.containerType}
            selectedWells={this.props.selectedWells}
            highlightedWells={this.state.highlightedWells}
            selectWells={this.props.selectWells}
            deselectWells={this.props.deselectWells}
            updateHighlightedWells={this.updateHighlightedWells}
            ingredNames={this.props.liquidNamesById} />
        </SingleLabwareWrapper>

        <WellSelectionInstructions />
      </div>
    )
  }
}

const mapStateToProps = (state: BaseState): SP => {
  const containerId = selectors.getSelectedLabwareId(state)
  const selectedWells = wellSelectionSelectors.getSelectedWells(state)
  if (containerId == null) {
    console.error('LiquidPlacementModal: No labware is selected, and no labwareId was given to LiquidPlacementModal')
    return {
      selectedWells: {},
      wellContents: {},
      containerType: '',
      liquidNamesById: {},
    }
  }

  const labware = selectors.getLabwareById(state)[containerId]
  let wellContents: ContentsByWell = {}

  // selection for deck setup: shows initial state of liquids
  wellContents = wellContentsSelectors.getWellContentsAllLabware(state)[containerId]

  return {
    selectedWells,
    wellContents,
    containerType: labware ? labware.type : 'missing labware',
    liquidNamesById: selectors.getLiquidNamesById(state),
  }
}

const mapDispatchToProps = (dispatch: Dispatch<*>): DP => ({
  deselectWells: (wells) => dispatch(deselectWells(wells)),
  selectWells: (wells) => dispatch(selectWells(wells)),
})

export default connect(mapStateToProps, mapDispatchToProps)(LiquidPlacementModal)
