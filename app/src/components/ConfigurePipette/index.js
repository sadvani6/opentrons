// @flow
import * as React from 'react'
import {connect} from 'react-redux'
import {Link} from 'react-router-dom'
import {makeGetRobotPipettes} from '../../http-api-client'

import {getPipetteModelSpecs} from '@opentrons/shared-data'

import {AlertModal} from '@opentrons/components'
import ConfigMessage from './ConfigMessage'

import type {State} from '../../types'
import type {Mount} from '../../robot'
import type {Robot} from '../../discovery'
import type {PipettesResponse} from '../../http-api-client'

type OP = {
  robot: Robot,
  mount: Mount,
  parentUrl: string,
}

type SP = {
  pipettes: ?PipettesResponse,
}

type Props = SP & OP

export default connect(makeMapStateToProps)(ConfigurePipette)

function ConfigurePipette (props: Props) {
  const {parentUrl, mount, pipettes} = props
  // TODO (ka 2019-2-12): This logic is used to get display name in slightly
  // different ways in several different files.
  const pipetteModel = pipettes && pipettes[mount].model
  const pipetteConfig = pipetteModel && getPipetteModelSpecs(pipetteModel)
  const displayName = pipetteConfig ? pipetteConfig.displayName : ''

  const TITLE = `Pipette Settings: ${displayName}`

  return (
    <AlertModal
      heading={TITLE}
      alertOverlay
      buttons={[{children: 'cancel', Component: Link, to: parentUrl}]}
    >
      <ConfigMessage />
    </AlertModal>
  )
}

function makeMapStateToProps (): (state: State, ownProps: OP) => SP {
  const getRobotPipettes = makeGetRobotPipettes()

  return (state, ownProps) => {
    const pipettesCall = getRobotPipettes(state, ownProps.robot)

    return {
      pipettes: pipettesCall && pipettesCall.response,
    }
  }
}
