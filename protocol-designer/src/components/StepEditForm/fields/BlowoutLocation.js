// @flow
import * as React from 'react'
import {connect} from 'react-redux'
import {DropdownField, type Options} from '@opentrons/components'
import cx from 'classnames'
import {selectors as stepFormSelectors} from '../../../step-forms'
import {selectors as uiLabwareSelectors} from '../../../ui/labware'
import {getBlowoutLocationOptionsForForm} from '../utils'
import FieldConnector from './FieldConnector'
import styles from '../StepEditForm.css'
import type {StepFieldName} from '../../../steplist/fieldLevel'
import type {BaseState} from '../../../types'
import type {FocusHandlers} from '../types'

// TODO: 2019-01-24 i18n for these options

type BlowoutLocationDropdownOP = {
  name: StepFieldName,
  className?: string,
} & FocusHandlers
type BlowoutLocationDropdownSP = {options: Options}
const BlowoutLocationDropdownSTP = (state: BaseState, ownProps: BlowoutLocationDropdownOP): BlowoutLocationDropdownSP => {
  const unsavedForm = stepFormSelectors.getUnsavedForm(state)
  const disposalLabwareOptions = uiLabwareSelectors.getDisposalLabwareOptions(state)
  const options = getBlowoutLocationOptionsForForm(disposalLabwareOptions, unsavedForm)

  return {options}
}
export const BlowoutLocationDropdown = connect(BlowoutLocationDropdownSTP)((props: BlowoutLocationDropdownOP & BlowoutLocationDropdownSP) => {
  const {options, name, className, focusedField, dirtyFields, onFieldBlur, onFieldFocus} = props
  return (
    <FieldConnector
      name={name}
      focusedField={focusedField}
      dirtyFields={dirtyFields}
      render={({value, updateValue, disabled}) => (
        <DropdownField
          className={cx(styles.medium_field, styles.orphan_field, className)}
          options={options}
          disabled={disabled}
          onBlur={() => { onFieldBlur(name) }}
          onFocus={() => { onFieldFocus(name) }}
          value={value ? String(value) : null}
          onChange={(e: SyntheticEvent<HTMLSelectElement>) => { updateValue(e.currentTarget.value) } } />
      )} />
  )
})

export default BlowoutLocationDropdown
