// @flow
import * as React from 'react'
import {connect} from 'react-redux'
import {FormGroup} from '@opentrons/components'
import cx from 'classnames'
import {selectors as stepFormSelectors} from '../../../../step-forms'
import styles from './WellOrderInput.css'
import stepEditStyles from '../../StepEditForm.css'
import WellOrderModal from './WellOrderModal'
import ZIG_ZAG_IMAGE from '../../../../images/zig_zag_icon.svg'
import type {BaseState} from '../../../../types'

type OP = {prefix: 'aspirate' | 'dispense' | 'mix'}
type SP = {iconClassNames: Array<string>}

type WellOrderInputState = {isModalOpen: boolean}
class WellOrderInput extends React.Component<OP & SP, WellOrderInputState> {
  state: WellOrderInputState = {isModalOpen: false}

  handleOpen = () => { this.setState({isModalOpen: true}) }
  handleClose = () => { this.setState({isModalOpen: false}) }

  render () {
    return (
      <FormGroup className={cx(styles.small_field, stepEditStyles.no_label)}>
        <WellOrderModal
          prefix={this.props.prefix}
          closeModal={this.handleClose}
          isOpen={this.state.isModalOpen} />
        <img
          onClick={this.handleOpen}
          src={ZIG_ZAG_IMAGE}
          className={cx(styles.well_order_icon, ...this.props.iconClassNames)} />
      </FormGroup>
    )
  }
}

const mapSTP = (state: BaseState, ownProps: OP): SP => {
  const formData = stepFormSelectors.getUnsavedForm(state)

  let iconClassNames = []
  if (formData) {
    const first = formData[`${ownProps.prefix}_wellOrder_first`]
    const second = formData[`${ownProps.prefix}_wellOrder_second`]
    iconClassNames = [styles[`${first}_first`], styles[`${second}_second`]]
  }
  return { iconClassNames }
}

export default connect(mapSTP)(WellOrderInput)
