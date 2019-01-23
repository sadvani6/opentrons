// @flow
import omit from 'lodash/omit'
import {combineReducers} from 'redux'
import {handleActions} from 'redux-actions'

import type {Wells} from '../labware-ingred/types'

type WellSelectionAction = {
  payload: Wells, // NOTE: primary wells.
}

type SelectedWellsState = {
  highlighted: Wells,
  selected: Wells,
}

function deleteWells (initialWells: Wells, wellsToRemove: Wells): Wells {
  // remove given wells from a set of wells
  return omit(initialWells, Object.keys(wellsToRemove))
}

// NOTE: selected wells state holds PRIMARY WELLS.
// The "primary well" is the well that the back-most tip of a multi-channel pipette goes into.
// For example, in the column A1, B1, C1, ... H1 in a 96 plate, A1 is the primary well.
const selectedWellsInitialState: SelectedWellsState = {highlighted: {}, selected: {}}
const selectedWells = handleActions({
  HIGHLIGHT_WELLS: (state, action: WellSelectionAction): SelectedWellsState =>
    ({...state, highlighted: action.payload}),

  SELECT_WELLS: (state, action: WellSelectionAction): SelectedWellsState => ({
    highlighted: {},
    selected: {...state.selected, ...action.payload},
  }),

  DESELECT_WELLS: (state, action: WellSelectionAction): SelectedWellsState => ({
    highlighted: {},
    selected: deleteWells(state.selected, action.payload),
  }),
  // Actions that cause "deselect everything" behavior:
  CLOSE_INGREDIENT_SELECTOR: () => selectedWellsInitialState,
  CLOSE_WELL_SELECTION_MODAL: () => selectedWellsInitialState,
  DESELECT_ALL_WELLS: () => selectedWellsInitialState,
  REMOVE_WELLS_CONTENTS: () => selectedWellsInitialState,
  SET_WELL_CONTENTS: () => selectedWellsInitialState,
}, selectedWellsInitialState)

export type RootState = {|
  selectedWells: SelectedWellsState,
|}

const rootReducer = combineReducers({
  selectedWells,
})

export default rootReducer
